from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from db import get_db
from deps import current_user
from models import Message, Project, User, Version
from schemas import MessageOut, ProjectDetail, ProjectOut, VersionOut

router = APIRouter()


@router.get("/projects", response_model=list[ProjectOut], response_model_by_alias=True)
def list_projects(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return db.scalars(
        select(Project)
        .where(Project.user_id == user.id)
        .order_by(Project.created_at.desc())
        .limit(50)
    ).all()


@router.get("/projects/{project_id}", response_model=ProjectDetail, response_model_by_alias=True)
def get_project(
    project_id: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    project = db.scalar(
        select(Project).where(Project.id == project_id, Project.user_id == user.id)
    )
    # Same 404 whether it doesn't exist or isn't yours.
    if project is None:
        raise HTTPException(404, "Not found.")

    versions = db.scalars(
        select(Version)
        .where(Version.project_id == project_id)
        .options(selectinload(Version.files))  # one query, not one per version
        .order_by(Version.n)
    ).all()
    messages = db.scalars(
        select(Message).where(Message.project_id == project_id).order_by(Message.created_at)
    ).all()

    return ProjectDetail(
        project=ProjectOut.model_validate(project),
        versions=[VersionOut.model_validate(v) for v in versions],
        messages=[MessageOut.model_validate(m) for m in messages],
    )
