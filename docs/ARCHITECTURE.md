# atoms-demo — Architecture

## The product in one paragraph

A user describes an app in a sentence. An agent writes it as a single
self-contained HTML document. It runs immediately in a sandboxed iframe. The
user can revise it in place; every revision is kept as a numbered version they
can jump back to, tagged with which model produced it.

---

## 1. The one decision everything else follows from

**The generated artifact is a single self-contained HTML document.**

Atoms-class tools generate multi-file projects and execute them in a container
or a browser VM. That is mostly infrastructure work — a sandboxed runtime to
provision, secure, and keep alive.

Constraining the agent to one HTML file with inlined CSS and JS means the
"runtime" is an `<iframe srcdoc>` with a `sandbox` attribute. The browser is the
sandbox. Nothing to operate.

| | Multi-file + container | Single HTML file |
|---|---|---|
| Preview | Container or WebContainer | `<iframe srcdoc sandbox>` |
| Infra | Orchestration, lifecycle, teardown | None |
| Generality | npm packages, server code, routing | Vanilla JS only, one page |
| Time to first working demo | Days | Hours |

The cost is real and worth stating plainly: **no npm packages, no server code, no
multi-page apps.** This is a deliberate trade, not an oversight. It buys the
headline experience — a real, interactive app appearing in seconds — at a
fraction of the cost.

Everything below is downstream of this.

---

## 2. Data model

Four auth tables (owned by the auth library) and two product tables.

```
┌──────────────┐
│ user         │   Google identity. One row per person.
│──────────────│
│ id      (PK) │
│ email        │
│ name         │
│ image        │
└──────┬───────┘
       │ 1
       │
       │ N
┌──────▼─────────────┐
│ projects           │   One app the user is building.
│────────────────────│
│ id          (PK)   │
│ user_id     (FK)   │──► user.id      ON DELETE CASCADE
│ title              │   derived from the first prompt
│ created_at         │
└──────┬─────────────┘
       │ 1
       │
       │ N
┌──────▼─────────────┐
│ versions           │   One generation. Append-only.
│────────────────────│
│ id          (PK)   │
│ project_id  (FK)   │──► projects.id  ON DELETE CASCADE
│ n                  │   1, 2, 3… monotonic within a project
│ prompt             │   what was asked at this step
│ html               │   the generated document
│ model_id           │   e.g. "deepseek/deepseek-chat"
│ created_at         │
└────────────────────┘
```

### Why it's shaped this way

**`project → versions[]`, versioned from the first commit.** The obvious v1
model is flat: one prompt, one output, one row. But "revise this app" is the
feature that makes the product feel alive, and bolting it onto a flat table
later means a migration *plus* a UI rewrite. Versioning up front costs one extra
table and makes iteration an `INSERT`.

**`versions` is append-only.** A revision never mutates a prior row. This is what
makes the version chips work: v1 through v4 all still exist, all still run, and
switching between them is a client-side array lookup with no server round trip.
Cheap to build, and it's the thing that makes the demo feel like a real tool.

**`model_id` lives on the version, not in UI state.** Which model built which
revision becomes part of the record. That enables a demo moment most submissions
won't have: build v1 with DeepSeek, v2 with Claude, same app, flip between them
and compare. A UI-only model setting cannot do this.

**`n` is per-project, not global.** Version numbers are meaningful to the user
("v3 of *my todo app*"), so they're scoped to the project. Computed as
`MAX(n) + 1` on insert.

**`title` is derived, not asked for.** Never ask a user to name something before
they've seen it. The first prompt, truncated, is a good enough label — and it's
one less field between intent and result.

### What's deliberately absent

- **No `deleted_at`.** Nothing is deleted in the demo.
- **No `is_current` flag on versions.** The current version is `MAX(n)`. A flag
  would be denormalised state that can disagree with the data.
- **No sharing / visibility column.** Every project is private to its owner.
  Adding public sharing later means one nullable `share_token` column — cheap,
  so it's not worth pre-building.

---

## 3. Data flow

### A. Sign in

```
Browser              Next.js               Google              DB
  │                     │                     │                 │
  │  GET /              │                     │                 │
  ├────────────────────►│                     │                 │
  │                     │ auth() → no session │                 │
  │  ◄── sign-in gate ──┤                     │                 │
  │                     │                     │                 │
  │  "Continue with     │                     │                 │
  │   Google" (form)    │                     │                 │
  ├────────────────────►│                     │                 │
  │                     │  redirect to Google │                 │
  │  ─────────────────────────────────────────►                 │
  │                     │                     │                 │
  │  ◄── consent screen ──────────────────────┤                 │
  │  approve                                  │                 │
  ├───────────────────────────────────────────►                 │
  │                     │                     │                 │
  │   /api/auth/callback/google?code=…        │                 │
  ├────────────────────►│                     │                 │
  │                     │ exchange code       │                 │
  │                     ├────────────────────►│                 │
  │                     │ ◄── id_token ───────┤                 │
  │                     │                     │                 │
  │                     │ upsert user, create session           │
  │                     ├──────────────────────────────────────►│
  │  ◄── redirect / + session cookie ─────────┤                 │
```

The session cookie is `httpOnly`. The browser never handles a token; every
subsequent request carries the cookie automatically.

### B. Build an app (the core loop)

```
Browser                Next.js API              Provider            DB
  │                        │                       │                 │
  │ POST /api/generate     │                       │                 │
  │ { prompt, modelId }    │                       │                 │
  ├───────────────────────►│                       │                 │
  │                        │                       │                 │
  │                        │ 1. auth() — who is this?                │
  │                        │    no session → 401                     │
  │                        │                       │                 │
  │                        │ 2. validate prompt, resolve modelId     │
  │                        │    unknown model → 400                  │
  │                        │                       │                 │
  │                        │ 3. call the model     │                 │
  │                        ├──────────────────────►│                 │
  │  (spinner)             │  ◄── raw text ────────┤                 │
  │                        │                       │                 │
  │                        │ 4. extract HTML from the reply          │
  │                        │    strip fences, find <!DOCTYPE         │
  │                        │    ─ doesn't look like HTML?            │
  │                        │      ONE strict retry ────────►│        │
  │                        │      still bad → 500 with a message     │
  │                        │                       │                 │
  │                        │ 5. INSERT project (title = prompt)      │
  │                        │    INSERT version (n=1, html, model_id) │
  │                        ├────────────────────────────────────────►│
  │                        │                       │                 │
  │  ◄─ { projectId, version } ───────────────────────────────────── │
  │                        │                       │                 │
  │ router.push(/p/:id)    │                       │                 │
  │ render <iframe srcdoc={html} sandbox>          │                 │
```

Step 4 is the load-bearing one. Models are told to emit raw HTML and nothing
else, and they mostly comply — but "mostly" is not a demo you can run in front of
an evaluator. The extractor strips markdown fences and any preamble, then
validates. A single strict retry catches almost everything the first pass misses.
Without this the app breaks maybe one build in ten, which is exactly often enough
to happen during a review.

### C. Revise (the same endpoint)

The only difference is that `projectId` is present in the request:

```
POST /api/generate { prompt: "add a dark mode toggle", projectId, modelId }
  │
  ├─ auth() → userId
  ├─ SELECT project WHERE id = :projectId AND user_id = :userId
  │    not found → 404          ← a project id in the URL is not authorisation
  ├─ SELECT version WHERE project_id = :projectId ORDER BY n DESC LIMIT 1
  │    → previous.html, n = previous.n + 1
  ├─ call model with (previous HTML + "revise it: <prompt>")
  ├─ INSERT version (n, html, model_id)      ← no UPDATE, ever
  └─ → { version }
```

One endpoint, two behaviours, switched by the presence of `projectId`. The
alternative — separate `/generate` and `/revise` routes — duplicates the auth
check, the extraction, the retry, and the insert. The ownership check and the
"latest version" lookup are the only branch.

### D. Load a saved project

`/p/:id` is a server component. It reads the session, fetches the project *and
its versions* in one server-side pass, and hands them to the client component as
props. The page arrives with the app already rendered — no loading flash, no
client fetch waterfall.

---

## 4. API contract

This is the seam. Any backend that satisfies it can serve the existing frontend.
(See `BACKEND-CHOICE.md`.)

### `GET /api/models`

Which models are actually usable right now. The picker is built from this, so a
provider with no API key configured simply doesn't appear — it can't be selected,
so it can't fail at generate time.

```json
{
  "models": [
    { "id": "deepseek/deepseek-chat", "label": "DeepSeek V3", "provider": "DeepSeek" },
    { "id": "anthropic/claude-sonnet", "label": "Claude Sonnet", "provider": "Anthropic" }
  ],
  "default": "deepseek/deepseek-chat"
}
```

### `POST /api/generate`

```json
// request
{ "prompt": "a pomodoro timer with a task list", "modelId": "deepseek/deepseek-chat" }
// ...or, to revise:
{ "prompt": "add a dark mode toggle", "projectId": "uuid", "modelId": "…" }

// 200
{
  "projectId": "uuid",
  "version": { "id": "uuid", "n": 1, "prompt": "…", "html": "<!DOCTYPE html>…", "modelId": "…" }
}
```

| Status | When |
|---|---|
| 400 | empty prompt, or a `modelId` that isn't available |
| 401 | no session |
| 404 | `projectId` doesn't exist **or isn't yours** — same response either way, so the endpoint doesn't leak which |
| 500 | the model failed twice, or the provider errored. Body carries a message the UI shows verbatim. |

### `GET /api/projects`

The current user's projects, newest first. Never anyone else's.

```json
[{ "id": "uuid", "title": "a pomodoro timer with a task list", "createdAt": "…" }]
```

### `GET /api/projects/:id`

One project and all its versions, ascending. 404 if it isn't yours.

```json
{ "project": { … }, "versions": [ { "n": 1, … }, { "n": 2, … } ] }
```

---

## 5. Trust boundaries

**The generated HTML is untrusted input.** A model wrote it, and a user's prompt
steered the model. It runs in an iframe with an explicit `sandbox` attribute
(`allow-scripts allow-forms allow-modals`) and *without* `allow-same-origin` —
so the frame gets a unique opaque origin and cannot touch the parent document,
its cookies, or its session. This is the single most important line in the
frontend.

The system prompt also forbids network calls and remote resources. That is a
quality instruction, not a security control — a model can ignore it. The sandbox
attribute is the control.

**A project id is not authorisation.** Every read and write of a project
re-checks `user_id` against the session server-side. Never trust the id in the
URL.

**API keys are server-side only.** The browser never sees a provider key. It
sends a `modelId`; the server resolves that to a provider, a base URL, and a key
from the environment.

---

## 6. Failure modes and what happens

| Failure | Behaviour |
|---|---|
| Model returns prose instead of HTML | Extractor strips it; if still invalid, one strict retry; then a readable error suggesting another model |
| Model times out | 500 with the provider's message; the version is never written, so no half-built rows |
| Provider key missing | The model never appears in the picker |
| Two builds fired at once | The button is disabled while `working`; the server would tolerate it (append-only, `MAX(n)+1`) but the UI prevents it |
| Generated app throws at runtime | It breaks inside the iframe only. The workbench is unaffected. The user reads the error, revises, gets v2. |

That last row is the point of the whole design. A broken generated app is a
normal, recoverable event — not an outage.
