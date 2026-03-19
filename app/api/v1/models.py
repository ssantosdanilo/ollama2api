from fastapi import APIRouter

from app.core.constants import TARGET_MODELS
from app.models.openai_models import ModelInfo, ModelList
from app.services.backend_manager import backend_manager

router = APIRouter()


@router.get("/models")
async def list_models():
    stats = backend_manager.get_stats()
    discovered = stats.get("models", [])

    all_models = list(set(TARGET_MODELS + discovered))
    all_models.sort()

    return ModelList(
        data=[ModelInfo(id=m, owned_by="ollama") for m in all_models]
    )
