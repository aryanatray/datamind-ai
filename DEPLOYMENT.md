# Deployment

## Local installation

Use Python 3.13, Docker with Linux containers and Compose. Native frontend development additionally requires Node.js 22.13+. Follow the root README to generate `.env`, start services, load isolated fixtures and build the application.

`setup.py` creates random secrets and never prints them. Keep `.env` local and provide `GEMINI_API_KEY` through an operator-controlled configuration. The template intentionally contains no key. Verify your selected model is available to your account.

## Services and ports

| Service | Published local port |
|---|---:|
| UI | 3000 |
| API and MCP | 18000 |
| PostgreSQL | 15432 |
| MySQL | 13306 |
| MongoDB | 17017 |
| Qdrant | 16333 |
| SearXNG | 18080 |

Published ports bind to loopback. Within Compose, use service names and their internal ports. For native Windows databases accessed from Docker, allow `host.docker.internal` and use their actual native ports, commonly 5432 and 27017.

## Startup and upgrades

```bash
docker compose up -d --wait postgres mysql mongo qdrant search
docker compose --profile app up -d --build
docker compose ps
```

The API, worker and migration service share `datamind-runtime:local`. For a controlled application upgrade, take backups first, then:

```bash
docker compose build api web
docker compose --profile app run --rm --no-deps migrate
docker compose --profile app up -d --no-deps --no-build api worker web
```

Check `http://localhost:3000/api/health` and `http://127.0.0.1:18000/api/health`. A healthy database response is not a Gemini or retrieval-readiness test. Inspect document processing and run a known read-only query separately.

## Operator bootstrap

Register the intended operator account and independently verify its identity. Read its user ID from the application database:

```bash
docker compose exec api python -c "from sqlalchemy import select; from sqlalchemy.orm import Session; from datamind.db import User, engine; db = Session(engine()); print(db.scalar(select(User.id).where(User.email == 'operator@example.com'))); db.close()"
```

Replace the example email with that registered account. If the result is `None`, stop and check the registration. Set `PLATFORM_ADMIN_EMAIL` and the returned `PLATFORM_ADMIN_USER_ID` in the host's local `.env`, then recreate API/worker. Merely matching an email address never grants platform administration. For native execution, `infrastructure/scripts/admin.py` provides the equivalent bootstrap against the configured application database.

On a Linux Docker host, the container UID `10001` needs write access to the bind-mounted `data/` directories. After generating fixtures, assign ownership or a narrow filesystem ACL; do not make storage world-writable. Native development instead needs access for the native process user.

## Backups

Back up the application database, `data/files`, the Qdrant volume and the encryption key together. File-version history is not a backup. Restore into an isolated environment and verify migrations, source grants, file downloads, stored credentials and known query/retrieval results before switching users over. Do not use `docker compose down -v` for routine restarts.

## Public deployment requirements

Before public exposure, configure TLS and the correct `PUBLIC_ORIGIN`, secure cookies, verified identity/SSO, least-privilege runtime database access, request/resource limits, parser containment, secrets management, monitoring and tested recovery. Keep infrastructure services off public interfaces. Review source destination allowlists and actual database TLS configurations. Allocate enough disk for images, model downloads, document revisions and backups.

Provider compatibility, the outstanding planning error and dependency advisories remain release gates. See [SECURITY.md](SECURITY.md).
