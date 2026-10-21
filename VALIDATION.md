# Validation

## Local baseline

| Check | Result |
|---|---|
| Backend, including isolated PostgreSQL/MySQL/MongoDB/Qdrant integration | 118 passed |
| Frontend unit tests | 3 passed |
| Non-provider Chromium workflows | 7 passed |
| Ruff lint and formatting | Passed |
| Frontend lint, TypeScript and production build | Passed |
| Compose configuration and application image builds | Passed |
| Application migration | 0002 |
| Web, API and proxied API health | HTTP 200 |
| Golden reference SQL | 40 of 40 matched fixture results |
| Local-model retrieval benchmark | 20 synthetic questions across four strategies |

These are local results, not a claim that hosted CI or a target deployment has passed. CI status is published by GitHub Actions for each commit.

## What is exercised

- Authentication, CSRF, tenant isolation, source grants, revocation and restricted history/exports/dashboards.
- SQL/Mongo safety checks, bounded query execution and genuine read-only fixture credentials.
- Upload validation, tabular imports, worker supersession, document replacement, historical downloads and restoration.
- Saved conversation scope, pins, personalization and access-aware history.
- Native provider request/schema fixtures, response parsing, actionable errors, sanitized diagnostics and bounded retries.
- Automatic public-search routing, disabled-Web behavior, empty-retrieval fallback and search outages.
- Browser setup, source selection, keyboard navigation, persistence, actual file processing, responsive layout and evidence inspection.

Streamed answer rendering in browser tests uses an explicitly mocked provider fixture. That fixture validates the UI contract; it does not prove live inference quality. Database execution and worker processing in the other workflows use real local services.

## Open gates

Live provider acceptance and the outstanding planning HTTP 400 require separately budgeted verification. Citation IDs are checked for existence; claim-level entailment and analytical correctness need representative answer grading. Native database TLS, hostile document containment, large-corpus/concurrent load, verified identity, disaster recovery and public deployment remain unverified. Known Pillow/FastEmbed dependency compatibility findings remain open; see SECURITY.md.

Synthetic templates are inspectable regression fixtures, not independent customer acceptance or evidence of general model accuracy.
