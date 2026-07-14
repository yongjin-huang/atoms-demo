from fastapi import APIRouter, Depends

from deps import current_user
from models import User
from providers import PROVIDERS, available_models, default_model_for
from schemas import ModelOut, ModelsOut

router = APIRouter()


@router.get("/models", response_model=ModelsOut)
def list_models(user: User = Depends(current_user)) -> ModelsOut:
    models = [
        ModelOut(id=m.id, label=m.label, provider=PROVIDERS[m.provider].label)
        for m in available_models(user)
    ]
    return ModelsOut(models=models, default=default_model_for(user))
