"""Serve an isolated synthetic console without constructing Application or a worker."""
from __future__ import annotations

import argparse
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from fastapi.responses import JSONResponse

from scripts.console_fixture import create_fixture
from sagacontext.config import Config
from sagacontext.console.service import ConsoleReadService
from sagacontext.daemon import create_app


def fixture_app(*, port: int = 37781, scenario: str = 'normal'):
    if scenario not in {'normal', 'empty', 'stale'}:
        raise ValueError('unknown_fixture_scenario')
    api = create_app()

    @asynccontextmanager
    async def lifespan(app):
        with TemporaryDirectory(prefix='sagacontext-console-') as directory:
            root = Path(directory)
            case = create_fixture(root, populated=scenario != 'empty')
            config = Config(state_path=root/'unused.db', ledger_path=case.path, port=port,
                            rollout_mode='guarded', rollout_worker_enabled=False,
                            rollout_stop_file=root/'STOP')
            app.state.runtime = SimpleNamespace(config=config)
            app.state.scheduler = None
            app.state.console = ConsoleReadService(case.path, case.owner_a)
            app.state.console_fixture = True
            app.state.fixture = case
            app.state.overview_reads = 0
            yield

    api.router.lifespan_context = lifespan

    @api.middleware('http')
    async def fixture_boundary(request, call_next):
        if request.method != 'GET' or not request.url.path.startswith('/console/'):
            return JSONResponse({'error': {'code': 'fixture_readonly'}}, status_code=404)
        if scenario == 'stale' and request.url.path.endswith('/overview'):
            api.state.overview_reads += 1
            if api.state.overview_reads > 1:
                return JSONResponse({'error': {'code': 'ledger_busy', 'retryable': True}}, status_code=503)
        return await call_next(request)

    return api


def main():
    import uvicorn
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=37781)
    parser.add_argument('--scenario', choices=['normal', 'empty', 'stale'], default='normal')
    args = parser.parse_args()
    uvicorn.run(fixture_app(port=args.port, scenario=args.scenario), host='127.0.0.1', port=args.port)


if __name__ == '__main__':
    main()
