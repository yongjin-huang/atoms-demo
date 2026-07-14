from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from db import get_db
from deps import current_user
from models import User
from providers import MODELS, PROVIDERS, available_models, default_model_for
from schemas import KeyStatus, ModelOut, SettingsIn, SettingsOut

router = APIRouter()


def _key_status(user: User) -> KeyStatus:
    return KeyStatus(
        deepseek=bool(user.deepseek_api_key),
        openai=bool(user.openai_api_key),
        anthropic=bool(user.anthropic_api_key),
        openrouter=bool(user.openrouter_api_key),
    )


def _configured(user: User) -> bool:
    keys = _key_status(user)
    return keys.deepseek or keys.openai or keys.anthropic or keys.openrouter


def _settings_out(user: User) -> SettingsOut:
    return SettingsOut(
        default_model_id=default_model_for(user) or user.default_model_id,
        configured=_configured(user),
        keys=_key_status(user),
        models=[
            ModelOut(id=m.id, label=m.label, provider=PROVIDERS[m.provider].label)
            for m in MODELS
        ],
    )


@router.get("/settings", response_model=SettingsOut, response_model_by_alias=True)
def get_settings(user: User = Depends(current_user)) -> SettingsOut:
    return _settings_out(user)


@router.put("/settings", response_model=SettingsOut, response_model_by_alias=True)
def update_settings(
    body: SettingsIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> SettingsOut:
    for attr in (
        "deepseek_api_key",
        "openai_api_key",
        "anthropic_api_key",
        "openrouter_api_key",
    ):
        value = getattr(body, attr)
        if value is not None:
            cleaned = value.strip()
            if cleaned:
                setattr(user, attr, cleaned)

    if body.default_model_id is not None:
        known_ids = {m.id for m in MODELS}
        if body.default_model_id not in known_ids:
            raise HTTPException(400, "Unknown default model.")
        user.default_model_id = body.default_model_id

    valid_ids = {m.id for m in available_models(user)}
    if user.default_model_id and user.default_model_id not in valid_ids:
        raise HTTPException(400, "Add that provider's API key before making it the default.")

    db.add(user)
    db.commit()
    db.refresh(user)
    return _settings_out(user)
