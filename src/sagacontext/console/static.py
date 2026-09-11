"""Same-origin packaged UI; unknown APIs and assets never fall back to HTML."""
from pathlib import Path
import re

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from .security import check_console_access

_PAGE = re.compile(
    r"projects/[^/.]+/workspaces/[^/.]+"
    r"(?:/(?:tasks|sessions|memories|batches|activity|rollouts)(?:/[^/.]+)?)?/?"
)
_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; "
    "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'",
    "Referrer-Policy": "no-referrer",
}


def mount_console_static(api: FastAPI, dist_path: Path) -> None:
    root = dist_path.resolve()

    @api.get('/console/{path:path}', include_in_schema=False,
             dependencies=[Depends(check_console_access)])
    def console_page(request: Request, path: str):
        if any(part in {'.', '..'} for part in path.split('/')) or '\\' in path:
            raise HTTPException(404)
        if path.startswith('assets/'):
            asset = (root / path).resolve()
            if root not in asset.parents or not asset.is_file():
                raise HTTPException(404)
            return FileResponse(asset, headers={**_HEADERS, 'Cache-Control': 'public, max-age=31536000, immutable'})
        if path and not _PAGE.fullmatch(path):
            raise HTTPException(404)
        index = root / 'index.html'
        if not index.is_file():
            return JSONResponse({'error': {'code': 'console_assets_missing', 'retryable': False}},
                                status_code=503, headers=_HEADERS)
        html = index.read_text(encoding='utf-8')
        if getattr(api.state, 'console_fixture', False):
            html = html.replace('<head>', '<head><meta name="console-fixture" content="synthetic">', 1)
        return HTMLResponse(html, headers={**_HEADERS, 'Cache-Control': 'no-store'})
