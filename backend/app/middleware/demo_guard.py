"""Demo mode middleware — blocks destructive operations in demo instances.

When DEMO_MODE=true, this middleware intercepts requests that would modify
real data (DELETE, password changes, backups/restores, SMTP config, etc.)
and returns a friendly 403 explaining that the action is disabled in demo mode.
"""

import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger("doc.demo")

# Paths that are always blocked in demo mode (exact or prefix match)
_BLOCKED_PATHS = {
    # Account / auth changes
    "/api/auth/change-password",
    "/api/auth/update-account",
    # Backup / restore (data destruction risk)
    "/api/backup/restore-from-upload",
    # Settings that could break the demo
    "/api/settings/smtp",
    "/api/settings/dji",
    "/api/settings/opensky",
    "/api/settings/opendronelog",
    "/api/settings/device-keys",
}

# Path prefixes blocked for DELETE method
_BLOCKED_DELETE_PREFIXES = [
    "/api/customers/",
    "/api/missions/",
    "/api/aircraft/",
    "/api/pilots/",
    "/api/backup/",
]

# Paths where PUT is blocked (config changes)
_BLOCKED_PUT_PATHS = {
    "/api/settings/smtp",
    "/api/settings/payment",
    "/api/settings/dji",
    "/api/settings/opensky",
    "/api/settings/opendronelog",
    "/api/settings/branding",
    "/api/backup/schedule",
}

DEMO_MESSAGE = "This action is disabled in demo mode. Deploy your own instance to unlock full functionality."


class DemoGuardMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        method = request.method

        # Block specific paths for POST/PUT
        if method in ("POST", "PUT") and path in _BLOCKED_PATHS:
            logger.info("Demo guard blocked %s %s", method, path)
            return JSONResponse(
                status_code=403,
                content={"detail": DEMO_MESSAGE},
            )

        # Block PUT on config paths
        if method == "PUT" and path in _BLOCKED_PUT_PATHS:
            logger.info("Demo guard blocked PUT %s", path)
            return JSONResponse(
                status_code=403,
                content={"detail": DEMO_MESSAGE},
            )

        # Block DELETE on data paths
        if method == "DELETE":
            for prefix in _BLOCKED_DELETE_PREFIXES:
                if path.startswith(prefix):
                    logger.info("Demo guard blocked DELETE %s", path)
                    return JSONResponse(
                        status_code=403,
                        content={"detail": DEMO_MESSAGE},
                    )

        # Block flight data purge
        if method == "POST" and path == "/api/flight-library/purge":
            logger.info("Demo guard blocked purge")
            return JSONResponse(
                status_code=403,
                content={"detail": DEMO_MESSAGE},
            )

        return await call_next(request)
