import logging

import httpx

from app.config import settings

logger = logging.getLogger("droneops.ollama")

SYSTEM_PROMPT_TEMPLATE = """You are a professional drone operations report writer for {company_name}, \
an FAA Part 107 certified drone operations company. Generate a detailed, client-facing \
after-action report based on the following mission data and operator notes.

Include these sections:
1. **Mission Overview** - Brief summary of the operation, date, location, and objective
2. **Area Coverage** - Description of the area searched/surveyed, including total acreage and terrain
3. **Flight Operations Summary** - Total flight time, total distance, number of flights, and aircraft used
4. **Key Findings** - What was observed or accomplished during the mission
5. **Recommendations** - Follow-up actions or suggestions for the client

Be professional, concise, and factual. Use specific numbers from the flight data provided. \
Write in third person. Do not fabricate data - only reference information provided."""


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
) -> str:
    """Generate a report narrative using Ollama."""

    # Build per-flight details (aircraft and altitude only)
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

    logger.info("LLM report generation starting for '%s' (%s)", mission_title, location)
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.post(
                f"{settings.ollama_base_url}/api/generate",
                json={
                    "model": settings.ollama_model,
                    "prompt": user_prompt,
                    "system": SYSTEM_PROMPT_TEMPLATE.format(company_name=company_name),
                    "stream": False,
                    "options": {
                        "temperature": 0.3,
                        "num_predict": 768,
                        # Use 6 of 8 threads — leaves headroom for the OS and other services
                        "num_thread": 6,
                        # Smaller context window — our prompts are well under 2k
                        "num_ctx": 1536,
                        # Batch size reduced to lower peak CPU load
                        "num_batch": 192,
                    },
                },
            )
            resp.raise_for_status()
            data = resp.json()
            response_text = data.get("response", "")
            logger.info("LLM report generated: %d chars", len(response_text))
            return response_text
    except httpx.TimeoutException:
        logger.error("Ollama request timed out after 300s for '%s'", mission_title)
        raise
    except httpx.HTTPStatusError as exc:
        logger.error("Ollama HTTP error %s: %s", exc.response.status_code, exc)
        raise
    except Exception as exc:
        logger.error("Ollama request failed: %s", exc, exc_info=True)
        raise


async def check_ollama_status() -> dict:
    """Check if Ollama is running and the model is available."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{settings.ollama_base_url}/api/tags")
            resp.raise_for_status()
            data = resp.json()
            models = [m["name"] for m in data.get("models", [])]
            return {
                "status": "online",
                "models": models,
                "configured_model": settings.ollama_model,
                "model_available": any(settings.ollama_model in m for m in models),
            }
    except Exception as e:
        logger.warning("Ollama status check failed: %s", e)
        return {"status": "offline", "error": str(e)}
