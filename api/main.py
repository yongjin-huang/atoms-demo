from contextlib import asynccontextmanager

from fastapi import FastAPI

from db import Base, engine
from routes import models, projects, settings, stream


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Fresh create_all on an empty database. Nothing is in production, so
    # there is no schema to migrate — if the schema here changes, reset the
    # volume:  docker compose down -v && docker compose up
    # Alembic arrives with the first real deployment.
    Base.metadata.create_all(bind=engine)
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
