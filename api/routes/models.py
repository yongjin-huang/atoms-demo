from fastapi import APIRouter

from providers import DEFAULT_MODEL_ID, PROVIDERS, available_models
from schemas import ModelOut, ModelsOut

router = APIRouter()


@router.get("/models", response_model=ModelsOut)
def list_models() -> ModelsOut:
    models = [
        ModelOut(id=m.id, label=m.label, provider=PROVIDERS[m.provider].label)
        for m in available_models()
    ]
    ids = {m.id for m in models}
    default = DEFAULT_MODEL_ID if DEFAULT_MODEL_ID in ids else (models[0].id if models else None)
    return ModelsOut(models=models, default=default)
