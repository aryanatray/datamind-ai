# Security

## Deployment boundary

The default deployment is for a trusted local machine and binds published services to loopback. It is not an internet-facing production profile. Public registration lacks email verification, recovery and SSO; infrastructure uses local operator bootstrap accounts. Review network/authentication policy and split runtime/migration/operator identities before broader deployment.

The local operator controls `.env`, encryption keys, allowed database hosts, storage/source directories, CA bundles and backups. Treat access to those files or the container host as privileged. Self-hosting stores local data but still sends selected question context to Google's API when chat is enabled.

## Enforced controls

- Argon2 passwords; random expiring server sessions stored by digest; HTTP-only SameSite cookies; CSRF and origin checks; authenticated MCP bearer tokens with one-hour expiry.
- Deny-by-default member source grants; workspace checks for every source; private conversation/dashboard ownership; live source-grant and source-version checks for history, exports, dashboards, citations and queries.
- A policy epoch interrupts streaming when permissions change. The browser discards partial answers on error/cancel. Previously delivered/copyable information cannot be erased from a user's device.
- Fernet-encrypted connection configuration; credentials excluded from catalogs, prompts, response payloads and audit details. The Gemini key is backend-only.
- Explicit destination allowlists; typed connection fields; no arbitrary driver URLs. Certificate chain/identity verification is required when TLS is enabled. Local disposable services deliberately use private Docker/loopback networking without TLS; TLS integration was not tested against a real certificate-enabled database.
- SQL AST and scope validation, restricted functions/resources, read-only grants/transactions, SQLite authorizer and filesystem confinement, execution timeouts, row and byte limits. Qdrant filters workspace/source/version on dense and sparse retrieval before fusion.
- Native MongoDB specification validation; no JavaScript, `$where`, `$function`, `$lookup`, `$unionWith`, writes or unsafe stages; bounded queries and no aggregation disk spill.
- Upload size/type/signature checks, archive expansion limits, macro rejection, randomized storage names and text-only previews. There is no malware/antivirus engine or OCR sandbox. Untrusted document parsing should run with container/host resource controls.
- Automatic public web is enabled by default with a visible per-conversation off switch. Only a bounded query from the current question is eligible; history, retrieved document text, catalogs and query results are never appended to search requests. Heuristic routing blocks known private terms, source names, emails, credentials and demo identifiers, and keeps ambiguous questions local. This classifier is not a general confidential-data detector: users should disable Web for sensitive work. Legacy API callers may still provide an explicit public query. Retrieved snippets remain untrusted and cannot broaden tool permissions.
- Per-user/workspace rate controls, centralized daily token quotas with atomic reservations, bounded provider retries and cancellation. On cancellation/failure without final usage, reservations remain conservative.
- CSV formula-injection escaping and Markdown without arbitrary HTML or model-generated script execution.

## Residual risks and boundaries

AST validation is not a database sandbox. Use genuine read-only source accounts and database-side resource limits. Authorized administrators can configure network destinations already allowed by the operator; do not broaden that list casually. User-defined functions/views may have database-specific effects, so use dedicated least-privilege schemas and test them.

Prompt rules reduce accidental misuse but cannot guarantee truthful answers. Server-side authorization and query validation remain independent of all model/retrieved instructions. Citation existence is validated; entailment, metric interpretation and inferred causation need evaluation and review.

No high-availability design, distributed rate-limit service, complete immutable audit archive, external secrets manager, full CSP policy or automated security certification is supplied. TLS, external reverse proxies, recovery workflows and backups need validation in their target environment.

Do not rotate `ENCRYPTION_KEY` casually: encrypted source credentials will become unreadable. A coordinated rotation must decrypt/re-encrypt all affected credentials under controlled operator access. Session logout revokes the credential used for that request (bearer first, otherwise browser cookie); independently issued tokens expire separately unless explicitly logged out.

## Reporting

For a locally hosted installation, report security issues privately to its operator. Do not publish keys, connection passwords, session tokens, private documents or query results in an issue. This source delivery has no public remote/security contact configured; repository maintainers should configure a private reporting channel before public release.


## Source scope, personalization and file history

Conversation scope and pins are private to their owner and workspace. Narrowing scope excludes earlier messages linked to unselected sources from model context. Personalization is account-specific and applies to answer presentation; it cannot grant tool/source access. Document replacements preserve the source identity and grants while incrementing the index version. Downloads require current source access, and restore requires workspace administration. Prior uploaded bytes are retained for authorized file history until source deletion; this is not a backup or tamper-proof audit archive.
