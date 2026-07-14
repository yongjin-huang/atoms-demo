# atoms-demo — Sandbox Preview: Design Spec

Status: proposal · Targets: multi-file generation + server-side preview
Providers in scope: **docker** (local/dev, self-hosted) and **e2b** (hosted, production)

---

## 0. Goal and non-goals

**Goal.** Generated apps stop being one HTML file. A version becomes a file set
with a manifest; when the manifest asks for a runtime beyond the browser, the
API boots a sandbox, installs, runs, and hands the UI a preview URL. The
engine is an interface with two initial providers — Docker and E2B — chosen by
configuration, extendable to more (Fly Machines, Modal, Firecracker) without
touching product code.

**Non-goals (this iteration).** Multi-service generated apps (their own
databases), collaborative editing, persistent user deployments (publish = a
later phase that reuses the same engine), autoscaling warm pools.

**Kept invariant.** `srcdoc` remains the fast path. A static single-file app
never touches a sandbox. The agent chooses the runtime via the manifest.

---

## 1. Data model

`versions.html` (one TEXT column) becomes a file set. Full snapshot per
version — storage-simple, reconstruction-free. Dedup can come later via a
content-addressed blobs table; do not build it now.

```sql
-- versions: existing table, two new columns
ALTER TABLE versions ADD COLUMN runtime  VARCHAR NOT NULL DEFAULT 'srcdoc';
        -- 'srcdoc' | 'sandbox'
ALTER TABLE versions ADD COLUMN manifest JSONB;
        -- null for legacy srcdoc versions; see §2 for shape
-- versions.html stays, nullable, for legacy rows and the srcdoc fast path

CREATE TABLE files (
  id         VARCHAR PRIMARY KEY,          -- uuid
  version_id VARCHAR NOT NULL REFERENCES versions(id) ON DELETE CASCADE,
  path       VARCHAR NOT NULL,             -- relative, validated (§2.3)
  content    TEXT    NOT NULL,
  sha256     VARCHAR NOT NULL,             -- future dedup hook; cheap now
  UNIQUE (version_id, path)
);

CREATE TABLE sandboxes (
  id             VARCHAR PRIMARY KEY,      -- uuid
  version_id     VARCHAR NOT NULL REFERENCES versions(id) ON DELETE CASCADE,
  user_id        VARCHAR NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  provider       VARCHAR NOT NULL,         -- 'docker' | 'e2b'
  external_id    VARCHAR,                  -- container id / e2b sandbox id
  status         VARCHAR NOT NULL,         -- state machine, §5
  preview_url    VARCHAR,
  error          TEXT,
  boot_log       TEXT,                     -- tail of install/build output
  created_at     TIMESTAMP NOT NULL DEFAULT now(),
  expires_at     TIMESTAMP NOT NULL,       -- hard TTL; reaper enforces
  last_active_at TIMESTAMP NOT NULL DEFAULT now()
);
CREATE INDEX ON sandboxes (status, expires_at);
CREATE UNIQUE INDEX one_live_sandbox_per_version
  ON sandboxes (version_id) WHERE status NOT IN ('stopped','expired','error');
```

**Revision semantics (copy-on-write).** On a revision turn the agent emits
*only changed files* (§2.2). The server materializes the new version by
copying the previous version's `files` rows and overwriting/adding the emitted
paths, honoring an explicit `delete` list in the manifest. Every version is
therefore self-contained; rollback is "boot version N," nothing to replay.

---

## 2. Agent protocol v2

### 2.1 Wire format (model output)

The single `===APP===` sentinel becomes a block protocol. Prose first, always
— unchanged philosophy: a greeting never reaches a sentinel.

```
Sure — a kanban board with drag and drop. React with Vite, state in
localStorage so it survives reloads.
===MANIFEST===
{"template": "vite-react", "entry": "npm run dev", "port": 5173,
 "delete": []}
===FILE package.json===
{ ... }
===FILE index.html===
...
===FILE src/App.jsx===
...
===END===
```

- `template`: `"static"` | `"vite-react"` | `"node"` (registry, §4.4).
  `"static"` + a single `index.html` ⇒ `runtime='srcdoc'`, no sandbox — the
  existing experience, exactly.
- `delete`: paths to remove relative to the previous version (revisions only).
- `===END===` is required; its absence after stream end = malformed ⇒ strict
  retry (same one-retry policy as today, with the same non-streaming fallback).

### 2.2 Context reconstruction (`_history_messages` v2)

Sending every file of every version back is not viable. Per turn the model
receives: the manifest + **file tree with per-file line counts** of the
current version, the full content of files the model *names* (a lightweight
`===NEED file,file===` request block is phase-2; until then, full content of
files under 120 lines and tree-only above), and the instruction that emitting
a file replaces it wholesale — no diffs, whole files only. Whole-file
replacement keeps the parser and storage trivial and is what current models do
reliably.

### 2.3 Validation (server-side, before anything runs)

- Path rules: relative, no `..`, no leading `/`, no `\`, printable ASCII,
  ≤ 200 chars, ≤ 64 files, ≤ 256 KB/file, ≤ 1.5 MB total.
- Manifest rules: known template, port in 1024–65535, entry from the
  template's allowlist (§4.4) — the model does not get to invent shell
  commands. This single rule is most of the injection surface.
- `static` must contain `index.html`; sandbox templates must contain the
  template's required files (e.g. `package.json`).

### 2.4 SSE vocabulary (API → browser)

| event | payload | replaces |
|---|---|---|
| `reason`, `chat`, `retry`, `error` | unchanged | — |
| `manifest` | parsed manifest | — |
| `file_open` | `{path}` | `code` |
| `file_chunk` | `{text}` | `code` |
| `file_close` | `{path, bytes}` | — |
| `done` | `{projectId, message, version{runtime, manifest, fileTree}}` | `done` |

The UI gets a per-file progress feed (file tree filling in live — a strictly
better spectacle than one scrolling blob) and the frame-batched flush from the
streaming fixes applies per active file.

---

## 3. SandboxEngine — the interface

One `Protocol`, provider-agnostic, mirroring the existing `providers.py`
registry pattern. Product code (routes, boot orchestration, repair loop)
imports only this.

```python
# api/sandbox/engine.py
from dataclasses import dataclass
from typing import AsyncIterator, Protocol

@dataclass(frozen=True)
class SandboxSpec:
    template: str                 # key into TEMPLATES (§4.4)
    files: dict[str, str]         # path -> content, already validated
    port: int
    env: dict[str, str]
    ttl_seconds: int              # hard lifetime, provider-enforced where possible
    cpu: float = 0.5
    memory_mb: int = 512

@dataclass(frozen=True)
class ExecResult:
    exit_code: int
    stdout_tail: str              # last 8KB — repair-loop food
    stderr_tail: str

@dataclass(frozen=True)
class Preview:
    url: str                      # what the iframe loads

class SandboxEngine(Protocol):
    name: str

    async def create(self, spec: SandboxSpec) -> str:
        """Provision and return an external_id. Files are in place on return."""

    async def exec(self, external_id: str, argv: list[str], timeout_s: int) -> ExecResult:
        """Run a finite step (install, build). argv comes from the template
        allowlist only — never from model output."""

    async def start(self, external_id: str, argv: list[str], port: int) -> Preview:
        """Launch the long-running server; return when the port answers
        (poll with backoff, bounded) or raise SandboxBootError."""

    async def logs(self, external_id: str) -> AsyncIterator[str]:
        """Tail combined output; feeds the UI boot log and the repair loop."""

    async def destroy(self, external_id: str) -> None:
        """Idempotent. Reaper calls this on TTL/idle expiry."""

class SandboxBootError(Exception):
    """User-facing message + captured log tail; triggers the repair loop."""
```

Registry and selection:

```python
# api/sandbox/registry.py
ENGINES: dict[str, SandboxEngine] = {}          # populated at startup
def engine() -> SandboxEngine:
    return ENGINES[settings.SANDBOX_PROVIDER]   # 'docker' | 'e2b'
```

**Design rules that keep providers swappable**
1. Providers never see model text. They receive validated files and
   allowlisted argv. All prompt-adjacent logic stays in `agent.py`.
2. Providers own *mechanism* (how to run a container); the orchestrator owns
   *policy* (what to run, when to give up, TTLs, quotas, the state machine).
3. Anything only one provider can do (Docker labels, E2B templates) stays
   inside that provider, configured by env — never leaks into the interface.

---

## 4. The two providers

### 4.1 DockerEngine (local/dev, self-hosted VPS)

- **Transport**: `aiodocker` against `DOCKER_HOST` (default: socket mount into
  the api container).
- **Image**: prebuilt `atoms-sandbox-node:20` — Node 20, npm cache pre-warmed
  with the template lockfile's packages, so `npm ci` is seconds not minutes.
  Built from `api/sandbox/images/node20/Dockerfile`, published alongside the
  app images.
- **Hardening per container**: dedicated bridge network `atoms-sbx` with
  inter-container communication disabled — generated apps must never reach
  `db`, `api`, or each other; `--cpus`, `--memory`, `--pids-limit 256`,
  `--cap-drop ALL`, `--security-opt no-new-privileges`, read-only rootfs with
  a tmpfs workdir, no volume mounts from the host.
- **Egress**: `npm ci` needs the registry. Default: allow egress on the sbx
  network but document it as the known hole; hardened option (env flag):
  an on-network registry proxy (verdaccio) and outbound default-deny.
- **Files in**: tar stream via `put_archive` — one call, no per-file chatter.
- **Preview routing**: dev = publish the app port to an ephemeral host port,
  `preview_url = http://localhost:{hostport}`. Single-host prod = Traefik/
  Caddy with wildcard DNS; the engine sets router labels, URL becomes
  `https://{sandbox_id}.{SANDBOX_BASE_DOMAIN}`. Both are contained inside
  this provider.
- **The honest caveat**: mounting the Docker socket makes the api container
  root-equivalent on its host. Acceptable on a dev machine or a dedicated
  sandbox VPS; not acceptable on shared infrastructure. This is precisely why
  E2B is the production provider and Docker is the local one.

### 4.2 E2BEngine (hosted, production)

- **Transport**: official `e2b` Python SDK; auth via `E2B_API_KEY`.
- Firecracker microVM isolation, TTL enforcement, and public HTTPS preview
  URLs (`sandbox.get_host(port)`) are the platform's job — the reasons to pay.
- `create` → `Sandbox.create(template=..., timeout=ttl)` + batched
  `files.write`; `exec` → `commands.run` with timeout; `start` → background
  command + the same port-poll-until-ready as Docker (shared helper);
  `destroy` → `kill()`, idempotent.
- Custom E2B template mirroring the Docker image (same Node, same warmed
  cache) so an app boots identically on both providers — this symmetry is the
  test that the interface is honest.
- Ops notes: per-sandbox cost means the reaper and idle timeout are money,
  not hygiene; map SDK/API failures into the same `SandboxBootError` so the
  repair loop and UI are provider-blind.

### 4.3 Provider selection

```
SANDBOX_PROVIDER=docker            # local default (docker-compose)
SANDBOX_PROVIDER=e2b               # production default
E2B_API_KEY=...                    # e2b only
DOCKER_HOST=unix:///var/run/docker.sock   # docker only
SANDBOX_BASE_DOMAIN=preview.example.com   # docker prod routing only
SANDBOX_TTL_SECONDS=900
SANDBOX_IDLE_SECONDS=300
SANDBOX_MAX_LIVE_PER_USER=2
```

Same code path in every environment; only the engine differs. Local dev needs
no E2B account; production needs no Docker socket.

### 4.4 Template registry (shared policy)

```python
@dataclass(frozen=True)
class Template:
    id: str
    steps: list[list[str]]        # finite exec() steps, in order
    serve: list[str]              # the long-running start() command
    port: int
    required: list[str]           # files that must exist

TEMPLATES = {
  "static":     Template("static", [], [], 0, ["index.html"]),   # srcdoc, no sandbox
  "vite-react": Template("vite-react",
                  steps=[["npm", "ci", "--no-audit", "--no-fund"]],
                  serve=["npm", "run", "dev", "--", "--host", "0.0.0.0"],
                  port=5173, required=["package.json", "index.html"]),
  "node":       Template("node",
                  steps=[["npm", "ci", "--no-audit", "--no-fund"]],
                  serve=["node", "server.js"],
                  port=3000, required=["package.json", "server.js"]),
}
```

The manifest's `entry` must equal the template's `serve` (or be omitted).
Commands are data owned by the server, not text owned by the model.

---

## 5. Lifecycle: state machine, orchestrator, reaper

```
requested → creating → installing → starting → ready
                 └─────────┴────────────┴──→ error
ready → (idle timeout | TTL | user stop | newer version boots) → stopped
any   → expired   (reaper found it past expires_at)
```

**Orchestrator** (`api/sandbox/orchestrator.py`, provider-blind): enforce the
per-user live cap (evict oldest), stop any live sandbox for the same project,
insert the row, then drive create → template steps → start, persisting each
transition plus the boot-log tail. On step failure: status `error`, log tail
saved, repair loop offered (§7).

**Status to the browser**: `GET /sandboxes/{id}/events` — an SSE stream of
`{status, bootLogTail, previewUrl?, error?}`, reusing the existing SSE
plumbing. Polling fallback: `GET /sandboxes/{id}`.

**Reaper**: a 30s asyncio loop in FastAPI's lifespan — destroy past
`expires_at` or idle past `SANDBOX_IDLE_SECONDS` (last_active_at is pinged by
the UI while the preview tab is visible), then mark rows. Startup
reconciliation: mark rows `expired` whose external resource is gone (crash
recovery). E2B's own timeout is the backstop when the API was down.

**Routes**:

```
POST   /versions/{id}/sandbox      → ensure live sandbox (idempotent), 202 + row
GET    /sandboxes/{id}             → row
GET    /sandboxes/{id}/events      → SSE status stream
DELETE /sandboxes/{id}             → stop now
```

---

## 6. Frontend: PreviewRuntime seam

```ts
interface PreviewRuntime {
  mount(version: VersionDetail): void;   // srcdoc: set iframe; sandbox: ensure+subscribe
  dispose(): void;
  onState(cb: (s: PreviewState) => void): void;
}
// PreviewState: {kind:"frame", srcdoc} | {kind:"url", url}
//             | {kind:"booting", status, logTail} | {kind:"error", message, logTail}
```

`SrcdocRuntime` wraps today's behavior verbatim. `SandboxRuntime` POSTs
ensure, subscribes to the status SSE, renders the boot log while
`creating/installing/starting` (the log *is* the loading indicator), then
swaps in `<iframe src={previewUrl} sandbox="allow-scripts allow-forms">`.
Different origin, so no same-origin grant is needed or given. The Stage
component picks the runtime from `version.runtime` and knows nothing else —
this is the concrete payoff of the Workbench decomposition, which should land
first.

---

## 7. Repair loop (now load-bearing)

Multi-file projects fail to build in ways single HTML files never did, so the
loop is a launch requirement, not polish:

1. A template step or `start()` fails ⇒ `SandboxBootError` with log tail.
2. Orchestrator marks `error`; UI shows the tail plus **"Fix it"** (one click,
   or auto-run once — env flag `SANDBOX_AUTOFIX=1`).
3. Fix = a synthetic revision turn: user-role message "The build failed:
   ```{stderr_tail}``` Fix it. Emit only the files that change." through the
   normal agent path ⇒ new version ⇒ boot again.
4. Cap: 2 automatic attempts per user prompt, then stop and say so. Every
   attempt is an ordinary version — visible, rollbackable, honest history.

---

## 8. Security summary

- Model output is data everywhere: files validated (§2.3), commands
  allowlisted (§4.4), env vars fixed by the server.
- Network: sandboxes must not reach `db`/`api`/each other (Docker: isolated
  bridge, icc off; E2B: isolated by construction). The internal API key never
  enters a sandbox env.
- Browser: preview iframes are cross-origin and sandboxed without
  `allow-same-origin`; the app's CSP gains `frame-src` for `localhost:*` (dev)
  / `*.{SANDBOX_BASE_DOMAIN}` / `*.e2b.dev`.
- Quotas: per-user live cap, TTL, idle stop, cpu/mem/pids limits — both
  providers, same numbers.
- Docker socket risk is confined to the Docker provider and documented as
  dev/self-hosted-only. Production posture = E2B.

---

## 9. Phasing

| Phase | Deliverable | Depends on |
|---|---|---|
| A | Workbench decomposition + `PreviewRuntime` seam (srcdoc only) | streaming fixes (done) |
| B | Schema v2 + agent protocol v2 + file-tree streaming UI; `static` template end-to-end — **no sandbox yet**, product visibly unchanged but multi-file-ready | A |
| C | Engine interface + **DockerEngine** + orchestrator/reaper + boot-log UI; `vite-react` template works locally | B |
| D | Repair loop (manual "Fix it", then autofix flag) | C |
| E | **E2BEngine** + provider selection by env; deploy with `SANDBOX_PROVIDER=e2b` | C |
| F | Publish = long-TTL sandbox or static export at a share URL | E |

Each phase ships independently; B is the point of no return for the data
model, so it carries the Alembic adoption (the "demo-managed schema" debt
comes due exactly here).
