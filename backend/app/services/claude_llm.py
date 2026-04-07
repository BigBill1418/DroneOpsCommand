import logging

import anthropic

from app.config import settings
from app.services.ollama import SYSTEM_PROMPT_TEMPLATE

logger = logging.getLogger("doc.claude_llm")

MODEL = "claude-sonnet-4-20250514"


async def generate_report(
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
    api_key: str = "",
) -> str:
    """Generate a report narrative using the Claude API."""

    resolved_key = api_key or settings.anthropic_api_key
    if not resolved_key:
        raise ValueError("No Anthropic API key configured — set it in Settings > AI / Report Generation")

    # Build per-flight details (same format as ollama.py)
    flight_details = ""
    for i, flight in enumerate(flight_summaries, 1):
        flight_details += f"\nFlight {i}:\n"
        flight_details += f"  Aircraft: {flight.get('aircraft', 'Unknown')}\n"
        flight_details += f"  Max Altitude: {flight.get('max_altitude', 'Unknown')}\n"
        if flight.get('notes'):
            flight_details += f"  Notes: {flight['notes']}\n"

    # Build mission-level totals
    totals = f"Number of Flights: {len(flight_summaries)}"
    if total_duration_seconds > 0:
        minutes = total_duration_seconds / 60
        totals += f"\nTotal Flight Time: {minutes:.0f} minutes"
    if total_distance_meters > 0:
        miles = total_distance_meters / 1609.344
        totals += f"\nTotal Distance Covered: {miles:.2f} miles"
    if ground_covered_acres:
        totals += f"\nEstimated Area Covered: {ground_covered_acres:.2f} acres"

    user_prompt = f"""Mission: {mission_title}
Date: {mission_date or 'Not specified'}
Type: {mission_type}
Location: {location}

Mission Totals:
{totals}

Flight Data:
{flight_details}

Operator Notes:
{user_narrative}

Generate the after-action report:"""

    logger.info("Claude report generation starting for '%s' (%s)", mission_title, location)
    try:
        client = anthropic.Anthropic(api_key=resolved_key)
        logger.info("Claude request: model=%s", MODEL)
        message = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=SYSTEM_PROMPT_TEMPLATE.format(company_name=company_name),
            messages=[{"role": "user", "content": user_prompt}],
            temperature=0.3,
        )
        response_text = message.content[0].text
        logger.info(
            "Claude report generated: %d chars, %d input tokens, %d output tokens",
            len(response_text),
            message.usage.input_tokens,
            message.usage.output_tokens,
        )
        return response_text
    except anthropic.AuthenticationError as exc:
        logger.error("Claude authentication failed — check API key: %s", exc)
        raise
    except anthropic.RateLimitError as exc:
        logger.error("Claude rate limit exceeded: %s", exc)
        raise
    except anthropic.APIError as exc:
        logger.error("Claude API error %s: %s", type(exc).__name__, exc)
        raise
    except Exception as exc:
        logger.error("Claude request failed: %s", exc, exc_info=True)
        raise
