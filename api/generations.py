"""Server-owned generations.

The model call belongs to the server, not to a browser tab. A generation is
spawned as a background thread, appends events to an in-memory log, persists
its result when it finishes — whether or not anyone is watching — and any
number of subscribers can replay-then-follow the log over SSE. Switching
conversations, refreshing, or losing Wi-Fi costs nothing: resubscribe with
`since=<seq>` and catch up.

Deliberate demo-scale choices, documented not hidden:
  - The log lives in process memory: one API instance. Redis pub/sub is the
    multi-process upgrade. A restart loses *running* generations (finished
    ones are already in Postgres).
  - Cancellation is cooperative — checked between model chunks.

agent/db imports happen inside the driver so this event machinery imports
(and tests) clean without SDKs or a database.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import AsyncIterator, Callable

TTL_SECONDS = 600     # finished generations linger for late subscribers
PING_SECONDS = 15     # SSE keepalive while idle


@dataclass
class Generation:
    id: str
    user_id: str
    project_id: str | None
    prompt: str
    model_id: str
    loop: asyncio.AbstractEventLoop
    wake: asyncio.Event
    status: str = "running"                       # running | done | error | stopped
    events: list[tuple[int, str, str]] = field(default_factory=list)
    finished_at: float | None = None
    cancel_flag: threading.Event = field(default_factory=threading.Event)

    # emit/finish are called from the driver thread; list.append is atomic
    # under the GIL and subscribers only ever read by slice.
    def emit(self, event: str, data: dict) -> None:
        self.events.append((len(self.events), event, json.dumps(data)))
        self.loop.call_soon_threadsafe(self.wake.set)

    def finish(self, status: str) -> None:
        self.status = status
        self.finished_at = time.monotonic()
        self.loop.call_soon_threadsafe(self.wake.set)

    @property
    def done(self) -> bool:
        return self.finished_at is not None


class Registry:
    def __init__(self) -> None:
        self._gens: dict[str, Generation] = {}

    def start(
        self,
        *,
        user_id: str,
        project_id: str | None,
        prompt: str,
        model_id: str,
        runner: Callable[[Generation], None] | None = None,   # injectable for tests
    ) -> Generation:
        gen = Generation(
            id=uuid.uuid4().hex,
            user_id=user_id,
            project_id=project_id,
            prompt=prompt,
            model_id=model_id,
            loop=asyncio.get_running_loop(),
            wake=asyncio.Event(),
        )
        self._gens[gen.id] = gen
        asyncio.create_task(asyncio.to_thread(runner or _drive, gen))
        return gen

    def get(self, gen_id: str) -> Generation | None:
        return self._gens.get(gen_id)

    def running_for_project(self, project_id: str) -> bool:
        return any(
            g.status == "running" and g.project_id == project_id
            for g in self._gens.values()
        )

    def active_for_user(self, user_id: str) -> list[Generation]:
        return [
            g for g in self._gens.values()
            if g.status == "running" and g.user_id == user_id
        ]

    async def subscribe(self, gen: Generation, since: int = 0) -> AsyncIterator[str]:
        """Replay events from `since`, then follow live. Terminates once the
        generation is finished and fully drained. Lost-wakeup-safe: clear,
        re-check, then wait."""
        local = max(0, since)
        while True:
            batch = gen.events[local:]
            for seq, event, data in batch:
                yield f"id: {seq}\nevent: {event}\ndata: {data}\n\n"
            local += len(batch)
            if gen.done and local >= len(gen.events):
                return
            gen.wake.clear()
            if len(gen.events) > local or gen.done:
                continue
            try:
                await asyncio.wait_for(gen.wake.wait(), timeout=PING_SECONDS)
            except asyncio.TimeoutError:
                yield ": ping\n\n"

    async def reap_forever(self) -> None:
        while True:
            await asyncio.sleep(30)
            now = time.monotonic()
            for gid, g in list(self._gens.items()):
                if g.done and g.finished_at is not None and now - g.finished_at > TTL_SECONDS:
                    self._gens.pop(gid, None)


registry = Registry()


def _drive(gen: Generation) -> None:
    """Worker thread: run the model, stream events into the log, persist.
    Owns its own DB session start to finish."""
    from sqlalchemy import func, select

    from agent import AgentError, converse, title_from
    from db import SessionLocal
    from models import File, Message, Project, User, Version
    from schemas import MessageOut, VersionOut

    db = SessionLocal()
    try:
        user = db.get(User, gen.user_id)

        turns: list[dict] = []
        previous_files: dict[str, str] | None = None
        if gen.project_id:
            turns = [
                {"role": m.role, "content": m.content, "version_id": m.version_id}
                for m in db.scalars(
                    select(Message)
                    .where(Message.project_id == gen.project_id)
                    .order_by(Message.created_at)
                ).all()
            ]
            latest = db.scalar(
                select(Version)
                .where(Version.project_id == gen.project_id)
                .order_by(Version.n.desc())
                .limit(1)
            )
            if latest is not None:
                previous_files = {f.path: f.content for f in latest.files}

        reasoning: list[str] = []
        chat = ""
        build: dict | None = None

        try:
            for kind, payload in converse(gen.model_id, user, gen.prompt, turns, previous_files):
                if gen.cancel_flag.is_set():
                    gen.emit("stopped", {})
                    gen.finish("stopped")
                    return
                if kind == "reason":
                    reasoning.append(payload)
                    gen.emit("reason", {"text": payload})
                elif kind == "chat":
                    gen.emit("chat", {"text": payload})
                elif kind == "manifest":
                    gen.emit("manifest", payload)
                elif kind == "file_open":
                    gen.emit("file_open", {"path": payload})
                elif kind == "code":
                    gen.emit("code", {"text": payload})
                elif kind == "file_close":
                    gen.emit("file_close", {"path": payload})
                elif kind == "retry":
                    gen.emit("retry", {})
                elif kind == "chat_done":
                    chat = payload
                elif kind == "build_done":
                    build = payload
        except AgentError as e:
            gen.emit("error", {"error": str(e)})
            gen.finish("error")
            return

        if gen.cancel_flag.is_set():
            gen.emit("stopped", {})
            gen.finish("stopped")
            return

        pid = gen.project_id
        if pid is None:
            project = Project(user_id=gen.user_id, title=title_from(gen.prompt))
            db.add(project)
            db.flush()
            pid = project.id

        db.add(Message(project_id=pid, role="user", content=gen.prompt))

        version = None
        if build is not None:
            # n is decided at save time, under this session — two generations
            # can't hand out the same number.
            latest_n = db.scalar(
                select(func.max(Version.n)).where(Version.project_id == pid)
            ) or 0
            version = Version(
                project_id=pid,
                n=latest_n + 1,
                prompt=gen.prompt,
                runtime=build["runtime"],
                manifest=build["manifest"],
                model_id=gen.model_id,
            )
            version.files = [
                File(path=p, content=c) for p, c in sorted(build["files"].items())
            ]
            db.add(version)
            db.flush()

        assistant = Message(
            project_id=pid,
            role="assistant",
            content=chat,
            reasoning="".join(reasoning) or None,
            model_id=gen.model_id,
            version_id=version.id if version else None,
        )
        db.add(assistant)
        db.commit()

        gen.project_id = pid
        gen.emit("done", {
            "projectId": pid,
            "message": MessageOut.model_validate(assistant).model_dump(by_alias=True, mode="json"),
            "version": (
                VersionOut.model_validate(version).model_dump(by_alias=True, mode="json")
                if version else None
            ),
        })
        gen.finish("done")
    except Exception as e:  # noqa: BLE001 — last resort; the log must terminate
        gen.emit("error", {"error": f"Generation failed: {e}"})
        gen.finish("error")
    finally:
        db.close()
