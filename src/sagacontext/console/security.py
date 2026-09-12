"""Local-only, same-origin console access. No change to operator endpoints."""
from urllib.parse import urlsplit
from fastapi import HTTPException, Request

LOOPBACK = {'127.0.0.1', 'localhost', '::1'}


def check_console_access(request: Request):
    config=request.app.state.runtime.config
    try:
        authority=urlsplit('http://'+request.headers.get('host',''))
        allowed=(config.host in LOOPBACK and authority.hostname in LOOPBACK
                 and authority.port==config.port and not authority.username and not authority.password
                 and not authority.path and not authority.query and not authority.fragment)
        origin=request.headers.get('origin')
        if origin:
            expected=f"{request.url.scheme}://{request.headers.get('host','')}"
            allowed=allowed and origin==expected
        if request.headers.get('sec-fetch-site')=='cross-site':
            allowed=False
    except ValueError:
        allowed=False
    if not allowed:
        raise HTTPException(403,detail={'code':'console_access_denied'})
