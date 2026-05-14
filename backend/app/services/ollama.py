import logging

import httpx

from app.config import settings

logger = logging.getLogger("doc.ollama")

SYSTEM_PROMPT_TEMPLATE = """You are a professional drone operations report writer for {company_name}, \
an FAA Part 107 certified drone operations company. Generate a formal, client-facing \
after-action report delivered FROM the operator TO the client.

AUDIENCE AND VOICE — STRICT REQUIREMENTS:
- The reader is the CLIENT who commissioned the flight. They are NOT the pilot.
- Write entirely in the third person. Refer to the pilot/PIC as "the operator" or \
"{company_name}". Refer to the aircraft as "the aircraft" or by model.
- NEVER address the operator. Do NOT use second person ("you", "your") to speak to \
the pilot. Do NOT issue coaching, critique, or instructions to the pilot. The \
operator's flying is NOT being graded or advised in this document.
- Operator Notes (provided below) are CONTEXT about what happened in the field. \
Translate them into third-person factual narrative. Do NOT echo them back as \
advice to the pilot. Phrases like "you should have", "next time consider", \
"we recommend you adjust", "the operator should have" are FORBIDDEN.

Include these sections, in order:
1. **Mission Overview** - Brief summary of the operation, date, location, and objective
2. **Area Coverage** - Description of the area surveyed, including total acreage and terrain
3. **Flight Operations Summary** - Total flight time, total distance, number of flights, and aircraft used
4. **Key Findings** - What was observed or accomplished during the mission, written as factual \
findings for the client
5. **Client Follow-Up Items** - OPTIONAL. Items requiring action by the CLIENT (e.g., \
"the marked area in Section 4 warrants on-the-ground inspection by the property owner"). \
This section is for CLIENT actions only. If no client action is warranted, OMIT this section \
entirely — do NOT fill it with pilot advice, technique tips, or operator self-critique. \
NEVER place pilot/aircraft/flight-technique recommendations here.

Be professional, concise, and factual. Use specific numbers from the flight data provided. \
Do not fabricate data — only reference information provided."""


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

Operator Notes (CONTEXT ONLY — translate into third-person factual narrative; \
do NOT address the operator or offer them advice):
{user_narrative}

Generate the client-facing after-action report. The reader is the client, not the \
pilot. Use third person throughout. Do not include pilot coaching."""

    logger.info("LLM report generation starting for '%s' (%s)", mission_title, location)
    try:
        async with httpx.AsyncClient(timeout=300, headers={"Connection": "close"}) as client:
            url = f"{settings.ollama_base_url}/api/generate"
            logger.info("LLM request: POST %s model=%s", url, settings.ollama_model)
            resp = await client.post(
                url,
                json={
                    "model": settings.ollama_model,
                    "prompt": user_prompt,
                    "system": SYSTEM_PROMPT_TEMPLATE.format(company_name=company_name),
                    "stream": False,
                    "options": {
                        "temperature": 0.3,
                        "num_predict": 1024,
                        # Use 4 of 6 threads — leaves headroom for other services
                        "num_thread": 4,
                        # Llama 3.1 benefits from slightly larger context
                        "num_ctx": 2048,
                        # Balanced batch size for throughput vs CPU load
                        "num_batch": 256,
                    },
                },
            )
            resp.raise_for_status()
            logger.info("LLM response: %d in %.1fs", resp.status_code, resp.elapsed.total_seconds())
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
