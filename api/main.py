from contextlib import asynccontextmanager

from fastapi import FastAPI

from db import Base, engine
from routes import models, projects, stream


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Creates missing tables only — it does NOT alter existing ones.
    # A real deployment gets Alembic. See docs/ARCHITECTURE.md.
    Base.metadata.create_all(bind=engine)
    yield


# No CORS, deliberately. Only the Next BFF talks to this, server-to-server.
app = FastAPI(title="atoms-demo api", lifespan=lifespan)

app.include_router(models.router)
app.include_router(projects.router)
app.include_router(stream.router)


@app.get("/health")
def health():
    return {"ok": True}
