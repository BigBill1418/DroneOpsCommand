from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import get_current_user
from app.database import get_db
from app.models.user import User
from app.services.llm_provider import get_llm_provider
from app.services.ollama import check_ollama_status

router = APIRouter(prefix="/api/llm", tags=["llm"])


@router.get("/status")
async def get_llm_status(
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Check LLM status based on the active provider."""
    provider = await get_llm_provider(db)

    if provider == "claude":
        return {
            "status": "online",
            "provider": "claude",
            "configured_model": "claude-sonnet-4-20250514",
            "model_available": True,
        }

    # Ollama provider — check live status
    result = await check_ollama_status()
    result["provider"] = "ollama"
    return result
