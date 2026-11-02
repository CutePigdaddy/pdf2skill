"""
pdf2skill single-entry web server (port 8501).

Serves the built demo frontend and the API proxy from one origin:

  - ``packages/app/dist``                 the Vite/TS workbench (build it first
                                          with ``npm run build``)
  - ``/api/*``                            forwarded to the thin proxy app in
                                          ``frontend/proxy_server.py`` (LLM and
                                          MinerU traffic; keys stay server-side)

For frontend development use ``python frontend/proxy_server.py`` (8502) plus
``npm run dev:app`` (Vite on 5173, which proxies ``/api`` to 8502).
"""

import os
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = Path(__file__).resolve().parent
DIST_DIR = PROJECT_ROOT / "packages" / "app" / "dist"
INDEX_FILE = DIST_DIR / "index.html"

# Importable both as `python frontend/server.py` and `frontend.server`.
if str(FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(FRONTEND_DIR))

from proxy_server import app as api_app  # noqa: E402

app = FastAPI(title="pdf2skill")

_ALLOWED_HOSTS = {"127.0.0.1:8501", "localhost:8501"}
_ALLOWED_HOSTS |= {
    h.strip().lower()
    for h in os.getenv("PDF2SKILL_ALLOWED_HOSTS", "").split(",")
    if h.strip()
}


@app.middleware("http")
async def check_host(request, call_next):
    """Same DNS-rebinding defense as proxy_server.py for the static page."""
    host = (request.headers.get("host") or "").lower()
    if host not in _ALLOWED_HOSTS:
        return PlainTextResponse(f"forbidden host: {host or '(missing)'}", status_code=403)
    return await call_next(request)


_MISSING_BUILD_HTML = """<!doctype html>
<html lang="zh-CN">
<meta charset="utf-8">
<title>pdf2skill — 前端尚未构建</title>
<body style="font-family:system-ui,sans-serif;max-width:680px;margin:80px auto;padding:0 20px;line-height:1.8">
<h1>前端尚未构建</h1>
<p>未找到 <code>packages/app/dist/index.html</code>。请在项目根目录运行：</p>
<pre style="background:#f5f6f8;padding:12px 16px;border-radius:8px">npm install
npm run build</pre>
<p>然后刷新本页。</p>
<p>也可以改用开发模式：<code>python frontend/proxy_server.py</code> +
<code>npm run dev:app</code>，访问 http://localhost:5173 。</p>
</body>
</html>
"""


@app.get("/")
def index():
    if not INDEX_FILE.is_file():
        return HTMLResponse(_MISSING_BUILD_HTML, status_code=503)
    # Hashed assets are cacheable; the shell must revalidate after a rebuild.
    return FileResponse(INDEX_FILE, headers={"Cache-Control": "no-store"})


app.mount("/assets", StaticFiles(directory=str(DIST_DIR / "assets"), check_dir=False), name="assets")

# Everything else (notably /api/*) is the proxy app. Mounted last so the
# static routes above take precedence.
app.mount("/", api_app)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8501)
