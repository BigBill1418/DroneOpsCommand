"""Website-lead source: the marketing API's DOC routes (ADR-0050 / marketing ADR-0110).

Logs carry lead keys and outcomes only — never names, emails or phone numbers.
"""
from __future__ import annotations

import logging
import re

import httpx

from app.config import settings

logger = logging.getLogger("doc.leads")

LEAD_KEY_RE = re.compile(r"^(web|cold)-\d{1,12}$")


class LeadSourceUnavailable(Exception):
    """Marketing API unreachable, misconfigured, or erroring."""


class LeadNotFound(Exception):
    """No website lead with that key."""


def is_enabled() -> bool:
    return bool(settings.leads_api_base and settings.leads_api_token)


class LeadSourceClient:
    def __init__(
        self,
        base: str,
        token: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 5.0,
    ) -> None:
        self._base = base.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}
        self._transport = transport
        self._timeout = timeout

    async def _request(self, method: str, path: str, **kw) -> dict:
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport, headers=self._headers
            ) as c:
                resp = await c.request(method, f"{self._base}{path}", **kw)
        except httpx.HTTPError as exc:
            logger.warning("[LEADS] %s %s transport error: %s", method, path, type(exc).__name__)
            raise LeadSourceUnavailable(type(exc).__name__) from exc
        if resp.status_code == 404:
            raise LeadNotFound(path)
        if resp.status_code == 401:
            logger.error("[LEADS] marketing API rejected the DOC token (401) — check LEADS_API_TOKEN")
        if resp.status_code >= 400:
            raise LeadSourceUnavailable(f"HTTP {resp.status_code}")
        try:
            return resp.json()
        except ValueError as exc:  # e.g. a proxy's HTML page with 200 (LD-2 M-4)
            logger.warning("[LEADS] %s %s returned non-JSON %s", method, path, resp.status_code)
            raise LeadSourceUnavailable("non-JSON response") from exc

    async def list_leads(
        self, q: str | None = None, include_closed: bool = False, limit: int = 25
    ) -> list[dict]:
        params: dict[str, str | int] = {"include_closed": "1" if include_closed else "0", "limit": limit}
        if q:
            params["q"] = q
        data = await self._request("GET", "/api/doc/leads", params=params)
        return list(data.get("leads", []))

    async def get_lead(self, key: str) -> dict:
        if not LEAD_KEY_RE.match(key):
            raise LeadNotFound(key)
        return await self._request("GET", f"/api/doc/leads/{key}")

    async def mark_won(self, key: str, mission_ref: str | None = None) -> dict:
        if not LEAD_KEY_RE.match(key):
            raise LeadNotFound(key)
        return await self._request(
            "POST", f"/api/doc/leads/{key}/won", json={"mission_ref": mission_ref}
        )


def get_client() -> LeadSourceClient:
    if not is_enabled():
        raise LeadSourceUnavailable("leads integration not configured")
    return LeadSourceClient(settings.leads_api_base, settings.leads_api_token)
