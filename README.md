# MarkItDown API

FastAPI backend for MarkItDown Pro. Convert uploaded files or documents at public URLs into Markdown using the `markitdown` library. An optional PostgreSQL database tracks successful conversions.

## Requirements

- Python 3.13 or newer (the repository pins 3.13).
- `uv` for installing the locked dependencies and running the server.
- PostgreSQL only if you want persistent conversion counts.

## Local setup

Run these commands from the backend directory:

```sh
uv sync --locked
uv run uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

The API runs at `http://127.0.0.1:8000`. Interactive documentation is available at `/docs`, with the OpenAPI schema at `/openapi.json`.

## Configuration

Set environment variables before starting the server. To load a local `.env` file explicitly, use:

```sh
uv run uvicorn main:app --env-file .env --reload --port 8000
```

The application does not load `.env` automatically. Environment files are ignored by Git; keep credentials out of committed files.

| Variable | Default | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | Empty | Optional PostgreSQL connection string, such as `postgresql://user:password@localhost:5432/markitdown`. |
| `CORS_ORIGINS` | `http://localhost:3000` | Comma-separated browser origins allowed to access the API. |
| `ALLOWED_HOSTS` | `*` | Comma-separated request hostnames; `*` allows all hosts. Use hostnames without schemes or ports. |
| `MAX_FILE_SIZE` | `50000000` | Maximum uploaded file size in bytes (50 MB by default). This limit does not apply to URL downloads. |
| `RATE_LIMIT_HEALTH` | `30/minute` | Per-client-IP rate limit for `/health`. |
| `RATE_LIMIT_CONVERT` | `10/minute` | Per-client-IP rate limit for `/convert`. |

When `DATABASE_URL` is set, startup creates the `conversions` table if needed, so the database user must have permission to create it. Each successful conversion inserts a timestamped row. Without a database, conversions still work and `/stats` returns zero. Counter read or write failures also fall back to zero or are ignored; database initialization failures can prevent startup.

## API

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/health` | Returns service status, application name, and version. |
| `GET` | `/stats` | Returns `{"total_conversions": 0}` or the persisted count. |
| `POST` | `/convert` | Accepts multipart form data with a `file` upload or a `url` field. |

### Convert a file

```sh
curl -X POST http://127.0.0.1:8000/convert -F "file=@document.pdf"
```

On Windows PowerShell, use `curl.exe` if `curl` resolves to a PowerShell alias.

Example response:

```json
{
  "markdown": "# Document\n\nExtracted text.",
  "filename": "document.pdf",
  "size": 12345
}
```

### Convert a URL

```sh
curl -X POST http://127.0.0.1:8000/convert -F "url=https://example.com/document.html"
```

URL responses include `markdown`, `filename`, and `source` (the submitted URL). When both fields are supplied, `url` takes precedence. The initial hostname is checked to reject private or internal network addresses; downloads follow redirects and use a 60-second HTTP client timeout.

Conversion support depends on `markitdown` and its installed dependencies. Common inputs include PDFs with readable text, Office documents, HTML, and text files. Scanned PDFs may need OCR before conversion; the backend does not configure an OCR service.

### Errors

Errors use FastAPI's `detail` field, except for host validation and rate-limit responses.

| Status | Meaning |
| --- | --- |
| `400` | Neither a file nor a URL was supplied, or the request host is not allowed. |
| `413` | The uploaded file exceeds `MAX_FILE_SIZE`. |
| `422` | URL validation, request validation, download, or conversion failed. |
| `429` | The endpoint's rate limit was exceeded. |

## Deployment

Install dependencies with `uv sync --locked`, set the environment variables for your deployment, and start the ASGI application:

```sh
uv run uvicorn main:app --host 0.0.0.0 --port 8000
```

Set the port to the value required by your hosting platform. Configure `CORS_ORIGINS` for the frontend's actual origin and `ALLOWED_HOSTS` for the backend's hostname. Rate limits use in-memory storage by default, so separate worker processes do not share their counters. `/health` reports service availability; it does not check database connectivity.
