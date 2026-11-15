# Connector Studio

AI Native Connector Platform — simulator-first, evidence-driven connector generation
and maintenance. Current slices implement WO-1 setup, WO-2 durable foundations,
WO-5 independent GitHub fixtures/verification scaffolding, WO-3 owner security, and WO-4 bounded source intake, and WO-6 deterministic REST runtime.

## Implemented

- React/TypeScript shell with a real API health check; separate API, worker,
  fixture service, and PostgreSQL 16. Web/API ports are loopback-only; the
  database, worker, and fixtures have no published ports.
- Versioned PostgreSQL migrations, retained source snapshots and artifact hashes,
  connectors/versions/operations, credential-reference-only connections, approvals,
  test reports, append-only audit records, invocation metadata, and deduplication.
- Transactional job claims, worker heartbeats, expiring/renewable leases, stale-worker
  fencing, retained attempts, and deadline-aware recovery. Persisted dispatched
  writes become `outcome_unknown` on interruption and are never auto-requeued.
- Immutable published artifacts/source links/operations and recorded test reports.
  Retention prunes only old terminal invocation metadata, with a minimum 30-day
  window; it preserves evidence, pinned versions, deduplication, and unknown writes.
- Independent **synthetic GitHub** service: list/get/create, mixed issue/pull-request
  pages, native pagination/errors/rate limits, and ambiguous writes. Documentation
  and provider revisions can change independently; public output stays `title`.
- Fixed public schemas/acceptance cases and a credential-free, network-isolated
  boundary probe harness. Hash-locked dependency scanning fails closed.

**Not implemented:** owner-login UI, model generation, build job orchestration, publication API,
MCP, workflows, or drift repair. Database job primitives are not a working build
pipeline. Fixture tests do not establish generated-adapter acceptance. No live
provider is invoked and no generated Python is executed.

## Local setup

Prerequisites: Docker Engine with Compose, GNU Make, and Python 3.10+ for host
verification scripts. Containers use Python 3.12 and Node 22.

1. Copy `.env.example` to `.env` and set a strong `POSTGRES_PASSWORD`. Never use
   an account password or commit `.env`.
2. Run `make dev` (API startup applies migrations under a database advisory lock).
3. Open http://127.0.0.1:5173. Non-sensitive API health is at
   http://127.0.0.1:8000/health. Owner-only connection/approval management is
   available; ordinary connector invocation remains unimplemented. Worker readiness
   also depends on its retained heartbeat; missing owner/encryption files degrade API health.

```sh
make verify-auth        # Owner/HTTP boundary tests plus actual session/secret restart
make verify-connections # AES-GCM secrets, rotation/revocation, safe fake-driver diagnostics
make verify-policy      # Resource denial, exact/concurrent single-use approvals, unknown writes
make verify-setup       # Configuration, actual processes/proxy, web build, pip consistency
make verify-restart     # Above plus a disposable PostgreSQL restart probe
make verify-durability  # PostgreSQL integration tests plus database/API/worker restart
make verify-fixtures    # Independent provider fixtures and fail-closed checker unit tests
make verify-security    # Isolated boundary probes and actual Python dependency audit
```

Restart checks interrupt services; do not run them during active work. Durability
checks use disposable UUID schemas and remove their test data, not application
records. `make status`, `make logs`, and `make stop` inspect or stop services without
removing data. Credentials initialize a fresh database volume only; changing `.env`
does not rotate an existing password. Use PostgreSQL's rotation procedure to retain
data. `docker compose down --volumes` destroys a disposable development database.

## Owner configuration and control boundary

`make dev` first runs explicit offline `make provision`. When app-owned credentials
are not supplied through encrypted workspace settings, it creates independent random
owner, vault-encryption, and future MCP gateway secrets in separate named volumes.
Directories are mode 0700, newly created files mode 0600, and the runtime account owns
them. Repeating provisioning preserves existing values. No value is printed or
committed. API mounts owner/vault files read-only; the worker mounts vault only;
fixtures, web, and isolated probe containers mount none of these secrets.

Only the operator can explicitly retrieve the owner credential in their own terminal:

```sh
docker compose run --rm --no-deps secret-init show-owner
```

Do not paste that output into chat or diagnostics. The backend requires secure owner
configuration; there is no anonymous/default-password fallback. `OWNER_ACCESS_TOKEN`
and `SECRET_ENCRYPTION_KEY` remain optional secure environment overrides; their
`*_FILE` alternatives point to protected files. Keep encrypted database backups and
vault-key backups together under appropriate protection. Deleting the vault volume
without restoring its key makes retained ciphertext unrecoverable. This is a local
prototype secret store, not a managed KMS or portable backup service.

Owner login: `POST /auth/login`, logout: `POST /auth/logout`, session: `GET /auth/session`.
Login requires the configured frontend Origin; mutations require that Origin, the
HttpOnly/SameSite Strict session cookie, and `X-CSRF-Token` from login/session. Sessions
are opaque, stored by hash, revocable, credential-epoch-bound, and expire after eight
hours. Validation errors do not echo inputs; failed logins are persistently rate-limited.
Body collection is bounded before credential-bearing validation. The login UI is WO-9.

Rotate a generated owner credential without displaying it:

```sh
docker compose run --rm --no-deps secret-init rotate-owner
```

Prior sessions invalidate on their next request. If an environment override is active,
rotate that secure setting instead and reload; it takes precedence over files.

Owner-only connection/approval routes store AES-256-GCM ciphertext bound to the
connection ID, opaque references, resource policies, and exact single-use approvals.
Resource policy changes/credential rotation revoke outstanding approvals. Trusted
fake-driver tests classify authentication/permission/reachability/unsupported-auth
errors without asking the generator to rewrite adapters. Policy denial precedes
credential resolution/provider contact. Actual protocol calls integrate in WO-6.
Live connections remain disabled until their later gates pass.

## Retained documentation and selections

Owner-only backend routes (login/Origin/CSRF above apply):

- `POST /sources/upload`: raw UTF-8 document body with its Content-Type and optional
  `X-Source-Name` header. This never reads a caller-specified host file path.
- `POST /sources/fetch`: JSON `url` and explicitly owner-approved `approved_origins`
  (scheme/host/port only). Each redirect is rechecked before contact. All resolved
  addresses must be permitted; the transport connects to the checked IP, validates
  its peer, and uses verified TLS/SNI without a second DNS lookup. No ambient proxy,
  cookie, authorization header, or automatic document-link crawling is used.
- `GET /sources/{source_id}`: retained origin/time/content digest and safe content.
- `POST /sources/proposals`: up to ten unique retained intake `source_ids`.
- `GET /sources/proposals/{proposal_id}`: immutable operations, schema/auth facts,
  source quotes/JSON pointers, and specific unresolved input requests.
- `POST /sources/proposals/{proposal_id}/selection`: immutable selected
  `operation_ids`; unknown, contradictory, or unresolved selections fail closed.

Limits: ten sources per proposal, 2 MiB per source/raw upload request, 100 operations,
32 parser levels, bounded nodes/reference expansion, three redirects, and a shared
15-second fetch deadline. DNS concurrency is bounded; HTTP responses are byte-limited
and compressed responses are rejected rather than inflated. Metadata, loopback and
private addresses are blocked, except RFC1918/ULA addresses for the exact app-owned
`http://fixtures:8000` alias; known IPv6 metadata addresses remain denied there too.

Deterministic extraction supports OpenAPI 3.0 JSON/YAML with bounded nonrecursive
local references, inherited parameters, and explicit no-auth/Bearer/Basic/API-key
facts. External/recursive references, ambiguous servers/authentication/responses,
unsupported schemas/formats and missing facts remain unresolved. YAML aliases,
custom executable tags, duplicate keys and excessive depth are rejected. HTML,
Markdown and text propose declared HTTP operations without executing scripts or
embedded instructions; absent structured schemas remain unresolved, not invented.
OpenAPI 3.1/Swagger and SOAP-specific extraction remain unsupported/deferred.

Configured/decrypted current credentials are registered for redaction in the trusted
intake process, including after restart; obvious credential examples/provider token
patterns are scrubbed too. Source content is sanitized **before** retention. Evidence
stores the retained-content digest plus the original-content digest/redaction flag,
not a plaintext credential-bearing original. All quotes refer to retained safe content.
Proposal/selection metadata is immutable and verified through actual process/database
restart. These tests establish the intake boundary, not model prompt-injection or
executable-adapter acceptance; those remain later generation/runtime gates.

## Fixture ownership and security gates

See [fixtures/README.md](fixtures/README.md) and [contracts/README.md](contracts/README.md).
The synthetic fixture credential is public test data, **not a real GitHub token**.
Fixture reset/scenario controls are internal to the private fixture service and
must never be attached to a public application router.

The trusted supervisor alone invokes Docker. Probe containers run non-root with
no host mounts, credentials, Docker socket, or network; read-only files/fixtures,
dropped capabilities, no-new-privileges, seccomp, 1 CPU, bounded memory/PIDs, a
small temporary filesystem, deadlines, and forced cleanup. Memory exhaustion must
actually OOM. On kernels without swap-limit support, verification only accepts
proven absence of swap plus enforced cgroup memory/PID limits; otherwise it blocks.

Actual scan evidence goes to ignored `.verification/security.json`; previous reports
are retained by digest in `.verification/history/`, including failures. The scanner
needs internet access to vulnerability databases; it is separate from isolated
probes, has no application secrets/mounts, and is cleaned up on timeout. Every
reported vulnerability, skipped dependency, missing report, or stale backend lock
blocks this prototype's gate. No exceptions are silently applied. This is a Python
package audit, **not** an OS-image or frontend vulnerability audit.

The checker also requires every fixed case and separately verified generated-adapter
and source-security evidence. Those latter checks are **pending**, so passing the
current fixture/security targets does not authorize publication or live credentials.
Metadata/private-host/redirect SSRF and instruction-injection attack inputs are
retained for later source/runtime integration, not represented as passed now.

## Software Factory workspace

`.8090/environment.json` uses the default image, installs the web lockfile, starts
`make dev`, and follows service logs. Supply `POSTGRES_PASSWORD` through encrypted
workspace settings, then reload the working branch. Docker/Compose must be available.
No secret belongs in tracked source or environment configuration. The owner selected Anthropic and supplied its key through encrypted workspace
settings; a reload applies it to the trusted worker only. Genuine generation and
model pricing/cost enforcement remain WO-7 gates, not acceptance of this security
slice. Incoming owner/MCP credentials remain separate from downstream credentials.

## Layout and next slices

- `backend/`: API/worker scaffolding, storage/recovery primitives, independent simulator.
- `backend/migrations/`: frozen forward migrations; destructive downgrades disabled.
- `frontend/`: setup shell only.
- `contracts/`, `fixtures/`: independently maintained public schemas/acceptance data.
- `tests/`: real PostgreSQL/process restart checks and provider/security probe tests.
- `tools/`: trusted isolated-runner supervisor and hash-locked audit tooling.

Next: deterministic
REST runtime (WO-6), genuine model-backed generation (WO-7), controlled publication
(WO-8), and the build UI (WO-9). Later milestones add Slack/MCP, legacy workflows,
and drift repair. Replay, simulator, and live evidence must stay clearly labeled.


## Deterministic REST runtime (WO-6)

Owner-authenticated `POST /invocations` takes `connection_id`, exact `version_id`,
`operation`, `public_major`, `arguments`, and optional `approval_id`.
`GET /invocations/{trace_id}` returns retained exact-version evidence. The active
selector deliberately returns `501 active_registry_pending` until WO-8. Ordinary
routes cannot select candidates, fixture scenarios, images, mounts or launch flags.

The M1 profile supports simulator GitHub list/get/create. It validates frozen
public contracts and the binding between public arguments and native repository,
issue, pagination, and write body before provider contact. Trusted drivers alone
resolve credentials, validate/pin destinations, inject Bearer authentication, and
contact the provider. Redirects are rejected, never followed with credentials.
Safe reads use at most three attempts within a shared 30-second deadline;
writes never auto-retry. Every write needs exact single-use approval and retained
pre-dispatch intent. Interrupted/ambiguous writes remain `outcome_unknown`;
even fresh approvals cannot repeat the same connection/operation/argument digest
until reconciliation. Blocked retry attempts themselves have status `failed` and
an `outcome_unknown` error; they do not claim a second provider effect occurred.
Reconciliation management is not yet exposed; unknown writes remain blocked.

Mapping evaluation is data-only, through PostgreSQL leases and a **host-only fixed
supervisor**, not in the API interpreter. `make dev` builds the evaluator image;
run `python3 tools/runtime_supervisor.py` in a separate foreground terminal when
using plain Compose. Software Factory starts this persistent terminal automatically.
No Docker socket is mounted into application or evaluation containers. Evaluators
have no network, credentials or mounts, run non-root/read-only, and have fixed
CPU/memory/PID/deadline constraints. `/health` separately reports
`mapping_evaluation` readiness; process health does not imply a build scheduler.
The five-slot actual invocation probe is `make verify-runtime`.

These probes use explicitly **handcrafted test mappings**, not model-generated
connectors or publication acceptance. Model-backed generation, publication gates,
UI flows, other protocol drivers and full lifecycle/performance acceptance remain
pending. Invocation overhead targets have not been measured or claimed satisfied.
See [the frozen mapping grammar](contracts/mapping-language.md).
