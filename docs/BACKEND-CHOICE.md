# Backend: Next.js only, or a Python service?

Written before committing, so the decision is a choice rather than a drift.

---

## What is actually being decided

The API contract in `ARCHITECTURE.md` §4 is fixed. The question is only *what
serves it*. Three options:

| | A. Next.js only | B. Next + FastAPI (proxied) | C. Next + FastAPI (direct) |
|---|---|---|---|
| Browser talks to | Next | Next only | Next **and** FastAPI |
| Generation runs in | Next route handler | Python | Python |
| Owns the schema | Drizzle (TS) | SQLAlchemy (Python) | SQLAlchemy (Python) |
| CORS | none | none | yes, must configure |
| Auth across the boundary | n/a | none needed — server-to-server | must forward + verify a token |
| Deployables | 1 | 2 | 2 |
| Hosts | Vercel | Vercel + Railway/Render/Fly | Vercel + Railway/Render/Fly |
| Extra build time | — | ~1.5–2 h | ~3 h |

**Option C is the one to avoid.** It's the shape people reach for by default, and
it's the most expensive: the browser holds a token, FastAPI has to verify it, and
you're configuring CORS and debugging preflights at hour six. It buys nothing
over B.

---

## Option B in detail — the version worth building

The trick is to stop thinking of Next as "the frontend" and start thinking of it
as the **BFF** (backend-for-frontend): it owns the browser session and nothing
else. Python owns the product.

```
                    ┌──────────────────────────────────────┐
  Browser ─────────►│  Next.js  (Vercel)                   │
   (cookie only)    │                                      │
                    │  • Google sign-in (Auth.js, JWT)     │
                    │  • serves the UI                     │
                    │  • /api/* → thin proxy               │
                    └───────────────┬──────────────────────┘
                                    │  server-to-server
                                    │  X-User-Id: <google sub>
                                    │  X-Internal-Key: <shared secret>
                                    ▼
                    ┌──────────────────────────────────────┐
                    │  FastAPI  (Railway / Render / Fly)   │
                    │                                      │
                    │  • provider registry + generation    │
                    │  • HTML extraction + retry           │
                    │  • owns ALL product tables           │
                    └───────────────┬──────────────────────┘
                                    ▼
                            ┌───────────────┐
                            │   Postgres    │
                            └───────────────┘
```

### The three things that make it cheap

**1. Auth.js drops its database adapter.**

Currently Auth.js uses database sessions and owns four tables. In this split it
switches to **JWT sessions** — no adapter, no tables, no Postgres access from
Next at all. It does the Google OAuth dance, gets the user's stable Google `sub`
and email, and puts them in a signed cookie. That's its entire job.

Python then owns *every* table, including `users`, keyed by the Google `sub`.
One schema, one migration tool, one language. This is a cleaner separation than
what we have today, not a messier one.

**2. The browser never talks to FastAPI.**

Next's `/api/*` routes become a proxy: read the session, attach the user id and
an internal shared secret as headers, forward to FastAPI, stream the response
back. Same origin, so no CORS, no preflight, no token in the browser.

FastAPI trusts `X-User-Id` **only** because it also verifies `X-Internal-Key`,
and because it isn't reachable from the public internet in any path the UI uses.
That's the whole auth story across the boundary — about fifteen lines.

```python
# api/deps.py
def current_user(
    x_user_id: str = Header(...),
    x_internal_key: str = Header(...),
) -> str:
    if not secrets.compare_digest(x_internal_key, settings.INTERNAL_KEY):
        raise HTTPException(401)
    return x_user_id          # upsert into users on first sight
```

**3. Docker Compose absorbs the extra service.**

Locally, "two services" costs one more block in a file you already have:

```yaml
services:
  db:   { … }                     # unchanged
  api:  { build: ./api, … }       # FastAPI, port 8000
  web:  { build: ., … }           # Next, port 3000, API_URL=http://api:8000
```

`docker compose up` still brings the whole thing up with one command. The local
story does not get worse.

### Where it does cost you

- **A second production deploy.** Vercel does not host FastAPI comfortably.
  You need Railway, Render, or Fly for the API, with its own env vars and its own
  URL. Budget an hour for the first successful deploy including the inevitable
  "it works locally" round.
- **A second dependency tree.** `requirements.txt`/`pyproject.toml` alongside
  `package.json`. Two lockfiles, two CI paths if you add CI.
- **Latency.** One extra network hop on every call. Irrelevant next to a
  10–30 second model call.

---

## What Python actually buys

Be honest about this, because it's the crux.

**It does not make the current feature set better.** Calling DeepSeek's
OpenAI-compatible endpoint and inserting two rows is equally trivial in either
language. If the app shipped today were the final state, Option A wins on every
axis.

**It buys headroom for where this kind of product goes next:**

- **Multi-step agent loops.** Generate → run → read the error → repair, in a
  loop with tool calls. Python's agent tooling (LangGraph, Pydantic AI, plain
  asyncio) is more mature than the TS equivalents, and this is the natural next
  feature.
- **Background work.** Generation is a 10–30 second operation. Today it's a
  blocking request, which is fine. The moment it becomes a queued job with
  progress streaming, Python has better answers (Celery, ARQ, or just asyncio
  tasks) than a Vercel route handler, which has a hard execution ceiling.
- **Anything numeric or ML-adjacent** — evaluating generated apps, embedding and
  searching past builds, scoring model outputs against each other.

**And it buys a demonstration.** The role is full-stack. A submission that shows
a considered service boundary, a typed contract between two languages, and a
working Compose file is showing more engineering surface than one that shows a
single Next app — *provided it's finished*. An unfinished split is worse than a
finished monolith on every criterion they listed.

---

## Recommendation

**Build Option B, but sequence it so the split is reversible.**

The insight is that the API contract is the seam. So:

1. **Ship the vertical slice on Next first** (the scaffold already does this).
   Prompt → generate → iframe → persist → revise, working end to end.
2. **Then port the two endpoints to FastAPI** (`/generate`, `/projects`) behind
   the same contract, and turn Next's routes into proxies.

If you run out of time at step 2, you ship a complete, working Next app and say
in the write-up that the Python port was scoped out. That's a defensible,
finished submission.

If you build the split first and run out of time, you ship a half-wired app that
doesn't run. That's the failure mode the sequencing above exists to prevent.

**The rule: never let the architecture decision be the thing that's unfinished.**

---

## If you commit to Python, the concrete shape

```
api/
  main.py           FastAPI app, CORS off, routes mounted
  deps.py           current_user() — the 15 lines above
  models.py         SQLAlchemy: users, projects, versions
  schemas.py        Pydantic request/response — mirrors §4 exactly
  providers.py      the registry (DeepSeek / OpenAI / OpenRouter / Anthropic)
  agent.py          generate_app(): call, extract HTML, one strict retry
  routes/
    generate.py
    projects.py
    models.py
  alembic/          migrations
  pyproject.toml
```

`providers.py` ports almost verbatim — the `openai` Python SDK takes a
`base_url` exactly like the TS one, so DeepSeek/OpenAI/OpenRouter still share a
single client, and Anthropic still gets the second adapter. The registry pattern
survives the language change unchanged, which is a decent sign it was the right
abstraction.

The frontend does not change at all. That is the point of writing the contract
down first.
