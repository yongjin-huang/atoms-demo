"""Generation lifecycle: start, watch (replay+follow), cancel.

POST /generate returns immediately — the model call runs server-side and
persists no matter what the browser does. Watching is a separate, resumable
subscription."""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from db import get_db
from deps import current_user
from generations import registry
from models import Project, User
from providers import default_model_for
from schemas import GenerateIn

router = APIRouter()


@router.post("/generate")
async def start_generation(
    body: GenerateIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    prompt = (body.prompt or "").strip()
    if not prompt:
        raise HTTPException(400, "Say something.")

    model_id = body.model_id or default_model_for(user)
    if not model_id:
        raise HTTPException(400, "Open settings and add an API key before building.")

    if body.project_id:
        project = db.scalar(
            select(Project).where(Project.id == body.project_id, Project.user_id == user.id)
        )
        if project is None:
            raise HTTPException(404, "Not found.")
        if registry.running_for_project(body.project_id):
            raise HTTPException(409, "A build is already running in this conversation.")

    gen = registry.start(
        user_id=user.id, project_id=body.project_id, prompt=prompt, model_id=model_id
    )
    return {"generationId": gen.id}


@router.get("/generations/active")
async def active_generations(user: User = Depends(current_user)):
    """Running generations for this user — how a freshly mounted page finds
    the stream it should reattach to."""
    return [
        {"id": g.id, "projectId": g.project_id, "prompt": g.prompt, "modelId": g.model_id}
        for g in registry.active_for_user(user.id)
    ]


@router.get("/generations/{gen_id}/events")
async def generation_events(
    gen_id: str, since: int = 0, user: User = Depends(current_user)
):
    gen = registry.get(gen_id)
    if gen is None or gen.user_id != user.id:
        raise HTTPException(404, "Not found.")
    return StreamingResponse(
        registry.subscribe(gen, since),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.delete("/generations/{gen_id}")
async def cancel_generation(gen_id: str, user: User = Depends(current_user)):
    gen = registry.get(gen_id)
    if gen is None or gen.user_id != user.id:
        raise HTTPException(404, "Not found.")
    gen.cancel_flag.set()
    return {"ok": True}
