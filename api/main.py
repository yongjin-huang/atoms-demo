from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text

from db import Base, engine
from routes import models, projects, settings, stream


def _ensure_demo_columns() -> None:
    """Demo-grade schema patching until real migrations exist."""
    statements = [
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS default_model_id VARCHAR",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS deepseek_api_key TEXT",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS openai_api_key TEXT",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS anthropic_api_key TEXT",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS openrouter_api_key TEXT",
    ]
    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Creates missing tables only — it does NOT alter existing ones.
    # A real deployment gets Alembic. See docs/ARCHITECTURE.md.
    Base.metadata.create_all(bind=engine)
    _ensure_demo_columns()
    yield


# No CORS, deliberately. Only the Next BFF talks to this, server-to-server.
app = FastAPI(title="atoms-demo api", lifespan=lifespan)

app.include_router(models.router)
app.include_router(projects.router)
app.include_router(settings.router)
app.include_router(stream.router)


@app.get("/health")
def health():
    return {"ok": True}
