# atoms-demo

Describe an app in plain language. An agent writes it as a single self-contained
HTML document. It runs immediately in a sandboxed frame. Revise it in place, and
every revision is kept as a numbered version you can jump back to — tagged with
which model built it.

## Run it

```bash
cp .env.example .env.local
npx auth secret                    # writes AUTH_SECRET
openssl rand -hex 32               # paste into INTERNAL_API_KEY
# add AUTH_GOOGLE_ID / AUTH_GOOGLE_SECRET and DEEPSEEK_API_KEY
docker compose up
```

- App: http://localhost:3000
- API docs: http://localhost:8000/docs

Google OAuth redirect URI, local:
`http://localhost:3000/api/auth/callback/google`

## Shape

```
Browser ──cookie──► Next.js (BFF)  ──X-User-Id + X-Internal-Key──►  FastAPI  ──►  Postgres
                    · Google sign-in                                 · providers
                    · serves the UI                                  · generation
                    · proxies /api/*                                 · owns every table
```

**Next owns the session and nothing else.** Auth.js runs with JWT sessions and
no database adapter — it does the Google dance, puts the `sub` claim in a signed
httpOnly cookie, and forwards it. It has no database connection at all.

**Python owns the product.** The provider registry, generation, HTML extraction,
the retry, and all three tables including `users`.

**The browser never talks to FastAPI.** Every call goes through Next's `/api/*`
proxy, server-to-server. Same origin, so there is no CORS, no token in the
browser, and no second thing to authenticate. FastAPI trusts `X-User-Id` only
because `X-Internal-Key` proves the request came from the BFF.

If you ever find yourself adding CORS middleware to the API, something is
calling it from a browser and the trust model has broken.

## Why a single HTML file

Atoms-class tools generate multi-file projects and run them in a container or a
browser VM. That is mostly infrastructure. Constraining the agent to one
self-contained HTML document makes the "runtime" an `<iframe srcdoc sandbox>` —
the browser is the sandbox, and there is nothing to operate.

The cost is generality: no npm packages, no server code, no multi-page apps. A
deliberate trade. See `docs/ARCHITECTURE.md`.

## Layout

```
app/          Next: pages + the /api/* proxy
components/   the workbench UI
lib/api.ts    the seam — the only path to the Python service
auth.ts       Google sign-in, JWT sessions, no database
api/          FastAPI: providers, generation, persistence
  agent.py      call the model, extract HTML, one strict retry
  providers.py  the registry — one line per model
  models.py     SQLAlchemy: users, projects, versions
docs/         architecture and the backend decision
```

## Known gaps

- **Schema is created with `create_all()` on startup.** Fine for a demo; a real
  deployment gets Alembic. Called out rather than hidden.
- No streaming. Generation is a blocking 10–30s request.
- No multi-file generation, no npm packages in generated apps.
- No sharing — every project is private to its owner.
