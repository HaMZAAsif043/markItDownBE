import time
from io import BytesIO
from pathlib import Path

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from markitdown import MarkItDown

app = FastAPI(title="MarkItDown API", version="0.1.0", description="Convert files to Markdown — unlimited & free")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

md = MarkItDown()

@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.1.0", "app": "MarkItDown"}

@app.post("/convert")
async def convert(file: UploadFile | None = None, url: str | None = Form(None)):
    if not file and not url:
        raise HTTPException(400, "Provide a file or a URL")
    try:
        if url:
            return await _convert_from_url(url)
        return await _convert_from_file(file)
    except HTTPException:
        raise
    except Exception as e:
        msg = str(e)
        if "not extract" in msg.lower() or "ocr" in msg.lower():
            raise HTTPException(422, "This file doesn't contain readable text. If it's a scanned PDF, run it through OCR first and try again.")
        raise HTTPException(422, f"Conversion failed: {msg}")

async def _convert_from_url(url: str) -> dict:
    async with httpx.AsyncClient(follow_redirects=True, timeout=60) as client:
        resp = await client.get(url)
        resp.raise_for_status()
    filename = Path(url.split("/")[-1] if "/" in url else url).name or "document"
    result = md.convert(BytesIO(resp.content))
    return {"markdown": result.text_content, "filename": filename, "source": url}

async def _convert_from_file(file: UploadFile) -> dict:
    content = await file.read()
    result = md.convert(BytesIO(content))
    return {"markdown": result.text_content, "filename": file.filename or "document", "size": len(content)}
