"""LLM provider dispatcher — routes report generation to Claude or Ollama.

When MANAGED_INSTANCE=true, always uses Claude regardless of DB settings.
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.system_settings import SystemSetting

logger = logging.getLogger("doc.llm_provider")


async def _get_setting(db: AsyncSession, key: str) -> str | None:
    """Read a single system setting from the database."""
    result = await db.execute(select(SystemSetting).where(SystemSetting.key == key))
    row = result.scalar_one_or_none()
    return row.value if row else None


async def get_llm_provider(db: AsyncSession) -> str:
    """Determine the active LLM provider.

    Managed instances always use Claude. Self-hosted checks DB then config.
    """
    from app.config import settings

    if settings.managed_instance:
        return "claude"

    provider = await _get_setting(db, "llm_provider")
    if provider and provider in ("claude", "ollama"):
        return provider
    return settings.llm_provider


async def generate_report(
    db: AsyncSession,
    user_narrative: str,
    mission_title: str,
    mission_type: str,
    location: str,
    flight_summaries: list[dict],
    ground_covered_acres: float | None = None,
    total_duration_seconds: float = 0,
    total_distance_meters: float = 0,
    mission_date: str | None = None,
    company_name: str = "DroneOps",
) -> str:
    """Generate a report using the configured LLM provider."""
    provider = await get_llm_provider(db)
    logger.info("LLM provider resolved to '%s'", provider)

    kwargs = dict(
        user_narrative=user_narrative,
        mission_title=mission_title,
        mission_type=mission_type,
        location=location,
        flight_summaries=flight_summaries,
        ground_covered_acres=ground_covered_acres,
        total_duration_seconds=total_duration_seconds,
        total_distance_meters=total_distance_meters,
        mission_date=mission_date,
        company_name=company_name,
    )

    if provider == "claude":
        from app.services.claude_llm import generate_report as claude_generate

        # Resolve API key from DB first, then config fallback
        api_key = await _get_setting(db, "anthropic_api_key") or ""
        return await claude_generate(**kwargs, api_key=api_key)
    else:
        from app.services.ollama import generate_report as ollama_generate

        return await ollama_generate(**kwargs)
