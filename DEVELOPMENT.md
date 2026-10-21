# Development

## Backend

Create and activate a Python 3.13 virtual environment, install `.[dev]`, and run `infrastructure/scripts/setup.py`. Start the Compose infrastructure and generate fixtures as described in the README.

For a native API using the local `.env` database configuration:

```bash
python -m alembic upgrade head
python -m uvicorn datamind.api:app --host 127.0.0.1 --port 18000
```

In another terminal with the same environment:

```bash
python -m datamind.worker
```

Native and Docker application databases are separate unless explicitly configured otherwise. Changing execution mode does not migrate accounts or encrypted source configurations. Keep encryption keys aligned with restored application data.

## Frontend

```bash
cd apps/web
npm ci
npm run dev
```

The default proxy targets `http://127.0.0.1:18000`. `API_URL` is consumed by the Next.js configuration; rebuild when changing a production proxy target. Use `PUBLIC_ORIGIN=http://localhost:3000` for the default browser origin.

## Tests

```bash
python -m ruff check apps/api tests datasets infrastructure/scripts
python -m ruff format --check apps/api tests datasets infrastructure/scripts
python -m pytest -q
```

Real service tests are opt-in. Generate/load the isolated Compose fixtures, set `RUN_SERVICE_TESTS=1`, then run pytest. In PowerShell, set an environment variable with `$env:RUN_SERVICE_TESTS='1'`.

```bash
cd apps/web
npm run lint
npm run build
npm run typecheck
npm test
```

For browser checks, start the complete app and use `E2E_DOCKER=1` when the API runs in Docker. Keep `RUN_LIVE_AI=0` for ordinary tests. The seven non-provider workflows can be selected with:

```bash
npm run test:e2e -- --grep-invert 'live Gemini|missing provider'
```

Provider inference is never required for unit tests. Live evaluation requires an explicit opt-in and a bounded budget. See [VALIDATION.md](VALIDATION.md).
