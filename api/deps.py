import secrets
from urllib.parse import unquote

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from db import get_db
from models import User
from settings import settings


def current_user(
    x_user_id: str = Header(...),
    x_internal_key: str = Header(...),
    x_user_email: str | None = Header(default=None),
    x_user_name: str | None = Header(default=None),
    x_user_image: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    """The whole auth story across the service boundary.

    Next has already verified the Google session; it forwards the user's stable
    Google `sub` as X-User-Id. We trust that header ONLY because X-Internal-Key
    proves the request came from our own BFF and not from a browser.

    Header values are percent-encoded by the BFF — names and image URLs are not
    guaranteed to be ASCII, and non-ASCII header bytes are not portable.
    """
    if not secrets.compare_digest(x_internal_key, settings.INTERNAL_API_KEY):
        raise HTTPException(401, "Not authorised.")

    user = db.get(User, x_user_id)
    if user is None:
        user = User(id=x_user_id)
        db.add(user)

    # Refresh the profile on every request — it's free, and it keeps the
    # avatar current without a separate sync path.
    if x_user_email:
        user.email = unquote(x_user_email)
    if x_user_name:
        user.name = unquote(x_user_name)
    if x_user_image:
        user.image = unquote(x_user_image)

    db.commit()
    return user
