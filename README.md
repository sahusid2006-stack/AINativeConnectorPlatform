# Connector Studio

AI Native Connector Platform — simulator-first, evidence-driven connector generation
and maintenance. Current slices implement WO-1 setup, WO-2 durable foundations,
and WO-5 independent GitHub fixtures and verification scaffolding.

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

**Not implemented:** owner login, secret storage, source ingestion, executable
connector mappings, model generation, actual job orchestration, publication API,
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
   http://127.0.0.1:8000/health. Management and connector invocation routes remain
   unavailable. Worker readiness also depends on its retained heartbeat.

```sh
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
No secret belongs in tracked source or environment configuration. No model or live
provider key is required yet. Later slices will request owner/encryption/model
credentials securely; incoming owner/MCP credentials remain separate from downstream
provider credentials.

## Layout and next slices

- `backend/`: API/worker scaffolding, storage/recovery primitives, independent simulator.
- `backend/migrations/`: frozen forward migrations; destructive downgrades disabled.
- `frontend/`: setup shell only.
- `contracts/`, `fixtures/`: independently maintained public schemas/acceptance data.
- `tests/`: real PostgreSQL/process restart checks and provider/security probe tests.
- `tools/`: trusted isolated-runner supervisor and hash-locked audit tooling.

Next: owner policy/secrets (WO-3), bounded source intake (WO-4), deterministic
REST runtime (WO-6), genuine model-backed generation (WO-7), controlled publication
(WO-8), and the build UI (WO-9). Later milestones add Slack/MCP, legacy workflows,
and drift repair. Replay, simulator, and live evidence must stay clearly labeled.
