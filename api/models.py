import uuid
from datetime import datetime

from sqlalchemy import String, Text, Integer, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class User(Base):
    """Keyed by the Google `sub` claim, which the Next BFF forwards."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    email: Mapped[str | None] = mapped_column(String, unique=True)
    name: Mapped[str | None] = mapped_column(String)
    image: Mapped[str | None] = mapped_column(String)
    default_model_id: Mapped[str | None] = mapped_column(String)
    deepseek_api_key: Mapped[str | None] = mapped_column(Text)
    openai_api_key: Mapped[str | None] = mapped_column(Text)
    anthropic_api_key: Mapped[str | None] = mapped_column(Text)
    openrouter_api_key: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    projects: Mapped[list["Project"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Project(Base):
    """A conversation, which may or may not have produced an app yet.

    Created on the first message — even a bare "hello" — so that a chat turn
    always has somewhere to live.
    """

    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped[User] = relationship(back_populates="projects")
    versions: Mapped[list["Version"]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="Version.n"
    )
    messages: Mapped[list["Message"]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="Message.created_at"
    )


class Version(Base):
    """A built app. Append-only. Not every turn produces one."""

    __tablename__ = "versions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    n: Mapped[int] = mapped_column(Integer)
    prompt: Mapped[str] = mapped_column(Text)
    html: Mapped[str] = mapped_column(Text)
    model_id: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    project: Mapped[Project] = relationship(back_populates="versions")


class Message(Base):
    """The transcript. NEW TABLE — create_all() will make it.

    An assistant message optionally points at the version that turn produced.
    A greeting produces a message with version_id = NULL, and no version at all.
    That's the whole reason this table exists: versions can't represent a turn
    that didn't build anything.
    """

    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String)  # "user" | "assistant"
    content: Mapped[str] = mapped_column(Text)
    reasoning: Mapped[str | None] = mapped_column(Text)   # the model's thinking, if it exposed any
    model_id: Mapped[str | None] = mapped_column(String)  # assistant turns only
    version_id: Mapped[str | None] = mapped_column(
        ForeignKey("versions.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    project: Mapped[Project] = relationship(back_populates="messages")
