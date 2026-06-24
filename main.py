import ipaddress
import os
import socket
from collections.abc import Awaitable, Callable
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse

import asyncpg
import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from markitdown import MarkItDown
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

DATABASE_URL = os.getenv("DATABASE_URL", "")
_pool = None
_config = {}

DEFAULT_CONFIG = {
    "rate_limit_health": os.getenv("RATE_LIMIT_HEALTH", "30/minute"),
    "rate_limit_convert": os.getenv("RATE_LIMIT_CONVERT", "10/minute"),
    "max_file_size": os.getenv("MAX_FILE_SIZE", "50000000"),
    "allowed_hosts": os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1"),
    "cors_origins": os.getenv("CORS_ORIGINS", "http://localhost:3000"),
}


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------
async def get_pool():
    global _pool
    if _pool is None and DATABASE_URL:
        _pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=3)
    return _pool


async def init_db():
    p = await get_pool()
    if not p:
        return
    async with p.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS conversions (
                id SERIAL PRIMARY KEY,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        for key, val in DEFAULT_CONFIG.items():
            await conn.execute(
                "INSERT INTO config (key, value) VALUES ($1, $2) ON CONFLICT (key) DO NOTHING",
                key, val,
            )


async def load_config():
    global _config
    _config = dict(DEFAULT_CONFIG)
    p = await get_pool()
    if not p:
        return
    async with p.acquire() as conn:
        rows = await conn.fetch("SELECT key, value FROM config")
        for row in rows:
            _config[row["key"]] = row["value"]


def cfg(key: str) -> str:
    return _config.get(key, DEFAULT_CONFIG[key])


async def increment_counter():
    try:
        p = await get_pool()
        if p:
            async with p.acquire() as conn:
                await conn.execute("INSERT INTO conversions (created_at) VALUES (NOW())")
    except Exception:
        pass


async def get_total_conversions() -> int:
    try:
        p = await get_pool()
        if p:
            async with p.acquire() as conn:
                row = await conn.fetchval("SELECT COUNT(*) FROM conversions")
                return row or 0
    except Exception:
        pass
    return 0


# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
limiter = Limiter(key_func=get_remote_address)

app = FastAPI(title="MarkItDown API", version="0.1.0", description="Convert files to Markdown")

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS must be registered before app starts — driven by env var only
cors_origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=cors_origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.on_event("startup")
async def startup():
    await init_db()
    await load_config()


@app.on_event("shutdown")
async def shutdown():
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


# ---------------------------------------------------------------------------
# Security headers + Host validation
# ---------------------------------------------------------------------------
@app.middleware("http")
async def security_headers(request: Request, call_next: Callable[[Request], Awaitable]) -> PlainTextResponse | JSONResponse:
    allowed = [h.strip() for h in cfg("allowed_hosts").split(",") if h.strip()]
    if allowed and request.url.hostname not in allowed:
        return PlainTextResponse("Invalid Host", status_code=400)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response


# ---------------------------------------------------------------------------
# SSRF guard
# ---------------------------------------------------------------------------
def _is_safe_url(url: str) -> bool:
    parsed = urlparse(url)
    if not parsed.hostname:
        return False
    try:
        info = socket.getaddrinfo(parsed.hostname, parsed.port or 80)
        for family, _, _, _, sockaddr in info:
            ip = ipaddress.ip_address(sockaddr[0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
                return False
    except OSError:
        return False
    return True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
md = MarkItDown()


async def _convert_from_url(url: str) -> dict:
    if not _is_safe_url(url):
        raise HTTPException(422, "URL points to a private or internal network — not allowed")
    async with httpx.AsyncClient(follow_redirects=True, timeout=60) as client:
        resp = await client.get(url)
        resp.raise_for_status()
    filename = Path(url.split("/")[-1] if "/" in url else url).name or "document"
    result = md.convert(BytesIO(resp.content))
    return {"markdown": result.text_content, "filename": filename, "source": url}


async def _convert_from_file(file: UploadFile) -> dict:
    content = await file.read()
    max_size = int(cfg("max_file_size"))
    if len(content) > max_size:
        raise HTTPException(413, f"File too large. Maximum allowed size is {max_size // 1_000_000} MB")
    result = md.convert(BytesIO(content))
    return {"markdown": result.text_content, "filename": file.filename or "document", "size": len(content)}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/health")
@limiter.limit(lambda: cfg("rate_limit_health"))
async def health(request: Request):
    return {"status": "ok", "version": "0.1.0", "app": "MarkItDown"}


@app.get("/stats")
async def stats():
    total = await get_total_conversions()
    return {"total_conversions": total}


@app.post("/convert")
@limiter.limit(lambda: cfg("rate_limit_convert"))
async def convert(request: Request, file: UploadFile | None = None, url: str | None = Form(None)):
    if not file and not url:
        raise HTTPException(400, "Provide a file or a URL")
    try:
        if url:
            result = await _convert_from_url(url)
        else:
            result = await _convert_from_file(file)
        await increment_counter()
        return result
    except HTTPException:
        raise
    except Exception as e:
        msg = str(e)
        if "not extract" in msg.lower() or "ocr" in msg.lower():
            raise HTTPException(422, "This file doesn't contain readable text. If it's a scanned PDF, run it through OCR first and try again.")
        raise HTTPException(422, f"Conversion failed: {msg}")
