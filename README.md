# Connector Studio

AI Native Connector Platform — simulator-first, evidence-driven connector generation
and maintenance. This initial change implements **WO-1 infrastructure only**.

## What's implemented

- Local React/TypeScript development shell with a real API health check.
- Separate health-only API, worker, and independent-fixture processes.
- PostgreSQL 16 with a persistent named volume and no published database port.
- Loopback-only web/API ports on an edge network, a private database/worker/fixture
  network, health checks, locked dependencies,
  secure configuration references, and setup smoke tests.

**Not implemented:** owner login, connector operations, application simulator
responses, model generation, MCP, job processing, publication, workflows, or drift
repair. The worker and fixture processes intentionally serve health only. A ready
health response proves infrastructure reachability, not product acceptance.

## Local setup

Prerequisites: Docker Engine with Docker Compose and GNU Make. Python 3.10+ is
needed for the host-side setup verification scripts; application containers use
Python 3.12 and Node 22. No host Python/Node packages are needed to start the app.

1. Copy `.env.example` to `.env` and set a new strong `POSTGRES_PASSWORD`.
   Never use a GitHub account password or commit `.env`.
2. Start the complete configured local stack:

   ```sh
   make dev
   ```

3. Open http://127.0.0.1:5173. The API's non-sensitive health endpoint is
   http://127.0.0.1:8000/health. No management or provider invocation endpoint exists.
4. Verify startup plumbing and the TypeScript production build:

   ```sh
   make verify-setup
   ```

`make verify-restart` additionally restarts PostgreSQL and confirms a disposable
probe survives; it removes the probe afterward. Do not run it during active work.

`make status` lists health, `make logs` shows process logs, and `make stop` stops
services **without deleting data**. PostgreSQL credentials initialize a fresh
volume only: changing the configuration does not rotate an existing database
password. Use PostgreSQL's password-rotation procedure when data must be retained.
For a disposable development database only, `docker compose down --volumes`
resets all data before restart.

## Software Factory workspace

This repository is the setup repository. `.8090/environment.json` uses the
platform default image, installs the web lockfile, starts Compose with `make dev`,
and follows the service logs in a configured terminal. Supply `POSTGRES_PASSWORD`
in encrypted workspace settings, then reload the working branch. Secrets are not
written to environment configuration or committed source. Docker/Compose must be
available in the workspace; a setup failure must be fixed rather than ignored.

No model or live-provider key is needed for this setup. Later work will request
owner/secret-storage credentials and model credentials securely before those
features run. Incoming owner/MCP credentials and downstream provider tokens stay
separate. Do not enable live provider tokens before security verification exists.

## Repository layout and development sequence

- `backend/`: health-only FastAPI scaffold; hash-locked Python dependencies.
- `frontend/`: React/TypeScript shell; npm lockfile and container build.
- `contracts/`: reserved for independent public schemas in WO-2/protocol slices.
- `fixtures/`: independent provider fixture ownership, implemented in WO-5 onward.
- `tests/`: configuration checks and actual process/proxy/build smoke verification.
- `compose.yaml`: local services and PostgreSQL durability.

Next: WO-2 durable evidence/job interfaces and WO-5 independent fixtures; WO-3
owner policy/secrets; source intake, deterministic runtime, model generation,
immutable publication, and the build UI. Subsequent milestones add Slack/MCP,
legacy workflows, and drift repair. `make verify-*` targets will be added alongside
actual features; unimplemented tests must not be represented as passing.

## Bootstrap walkthrough

Run the setup command, observe that the web health check reaches PostgreSQL
through the real API, run `make verify-setup`, stop and restart without removing
the volume. This demonstrates local infrastructure only. The full eight-operation
and maintenance walkthrough belongs to later work; replay and pending live
verification must always be labeled explicitly.
