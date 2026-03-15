from fastapi import APIRouter, Depends

from app.auth.jwt import get_current_user
from app.models.user import User
from app.services.ollama import check_ollama_status

router = APIRouter(prefix="/api/llm", tags=["llm"])


@router.get("/status")
async def get_llm_status(_user: User = Depends(get_current_user)):
    """Check Ollama LLM status and model availability."""
    return await check_ollama_status()
