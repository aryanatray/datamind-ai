# Contributing

Keep changes focused, preserve source-permission boundaries and add meaningful regressions for changed behavior. Review ARCHITECTURE.md and SECURITY.md before modifying connectors, indexing, orchestration or identity.

## Development checks

Run Python lint/format checks and affected tests. For frontend changes, run lint, production build, type checks and unit tests. Exercise relevant browser workflows when changing interactions. Use synthetic fixtures and mocked provider responses for ordinary tests; live provider calls require explicit opt-in and a bounded budget.

## Database and storage changes

Create an Alembic migration for application schema changes. Preserve existing uploads and source grants. Test upgrades and version-invalidated artifacts. Avoid destructive downgrade paths and never run fixture loaders against production databases.

## Security and commits

Never commit `.env`, keys, passwords, tokens, customer documents, runtime databases, model weights or raw private evaluation output. `.env.example` must retain blank credential fields. Keep author identity accurate and describe concrete behavior and executed validation in pull requests.

Report vulnerabilities privately to the repository owner through GitHub's private reporting options when available. Do not include credentials or confidential source content in a public issue.
