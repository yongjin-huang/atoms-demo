import json
from typing import Iterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from agent import AgentError, converse, title_from
from db import SessionLocal, get_db
from deps import current_user
from models import Message, Project, User, Version
from providers import default_model_for
from schemas import GenerateIn, MessageOut, VersionOut

router = APIRouter()


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@router.post("/generate/stream")
def generate_stream(
    body: GenerateIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """SSE: reason* chat* code* [retry] → done | error.

    A turn may be pure conversation, in which case no version is written.
    Ownership resolves before the response starts — once bytes flow, the status
    code is committed.
    """
    prompt = (body.prompt or "").strip()
    if not prompt:
        raise HTTPException(400, "Say something.")

    model_id = body.model_id or default_model_for(user)
    if not model_id:
        raise HTTPException(400, "Open settings and add an API key before building.")
    user_id = user.id
    project_id = body.project_id

    turns: list[dict] = []
    previous_html: str | None = None
    next_n = 1

    if project_id:
        project = db.scalar(
            select(Project).where(Project.id == project_id, Project.user_id == user_id)
        )
        if project is None:
            raise HTTPException(404, "Not found.")

        turns = [
            {"role": m.role, "content": m.content, "version_id": m.version_id}
            for m in db.scalars(
                select(Message)
                .where(Message.project_id == project_id)
                .order_by(Message.created_at)
            ).all()
        ]

        latest = db.scalar(
            select(Version)
            .where(Version.project_id == project_id)
            .order_by(Version.n.desc())
            .limit(1)
        )
        if latest is not None:
            previous_html = latest.html
            next_n = latest.n + 1

    def events() -> Iterator[str]:
        reasoning: list[str] = []
        chat = ""
        html: str | None = None

        try:
            for kind, payload in converse(model_id, user, prompt, turns, previous_html):
                if kind == "reason":
                    reasoning.append(payload)
                    yield _sse("reason", {"text": payload})
                elif kind == "chat":
                    yield _sse("chat", {"text": payload})
                elif kind == "code":
                    yield _sse("code", {"text": payload})
                elif kind == "retry":
                    yield _sse("retry", {})
                elif kind == "chat_done":
                    chat = payload
                elif kind == "code_done":
                    html = payload
        except AgentError as e:
            yield _sse("error", {"error": str(e)})
            return
        except Exception as e:
            yield _sse("error", {"error": f"Something went wrong: {e}"})
            return

        # A fresh session — the request-scoped one isn't guaranteed open once
        # the body is streaming.
        try:
            with SessionLocal() as s:
                pid = project_id
                if pid is None:
                    project = Project(user_id=user_id, title=title_from(prompt))
                    s.add(project)
                    s.flush()
                    pid = project.id

                s.add(Message(project_id=pid, role="user", content=prompt))

                version = None
                if html is not None:
                    version = Version(
                        project_id=pid, n=next_n, prompt=prompt, html=html, model_id=model_id
                    )
                    s.add(version)
                    s.flush()

                assistant = Message(
                    project_id=pid,
                    role="assistant",
                    content=chat,
                    reasoning="".join(reasoning) or None,
                    model_id=model_id,
                    version_id=version.id if version else None,
                )
                s.add(assistant)
                s.commit()

                yield _sse("done", {
                    "projectId": pid,
                    "message": MessageOut.model_validate(assistant).model_dump(
                        by_alias=True, mode="json"
                    ),
                    "version": (
                        VersionOut.model_validate(version).model_dump(by_alias=True, mode="json")
                        if version
                        else None
                    ),
                })
        except Exception as e:
            yield _sse("error", {"error": f"Replied, but could not save: {e}"})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
