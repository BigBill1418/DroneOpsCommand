from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql+asyncpg://doc:changeme@db:5432/doc"

    # Redis
    redis_url: str = "redis://redis:6379/0"

    # Ollama
    ollama_base_url: str = "http://ollama:11434"
    ollama_model: str = "qwen2.5:3b"

    # Claude (Anthropic)
    anthropic_api_key: str = ""
    # Current default: Sonnet 4.6 (2026 generation). Override via
    # CLAUDE_MODEL env var when bumping or pinning. Don't hardcode model
    # IDs in service modules — older snapshots are retired periodically
    # by Anthropic and the resulting model-not-found error otherwise
    # surfaces in the UI as a generic "report failed" toast.
    claude_model: str = "claude-sonnet-4-6"

    # LLM provider selection: "ollama" or "claude"
    llm_provider: str = "ollama"

    # OpenDroneLog
    opendronelog_url: str = ""

    # JWT
    jwt_secret_key: str = "changeme_generate_a_random_secret"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 30
    jwt_refresh_token_expire_days: int = 30

    # Demo mode admin (only used when DEMO_MODE=true)
    demo_admin_username: str = ""
    demo_admin_password: str = ""

    # SMTP
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from_email: str = ""
    smtp_from_name: str = ""
    smtp_use_tls: bool = True

    # File storage
    upload_dir: str = "/data/uploads"
    reports_dir: str = "/data/reports"

    # Trusted-proxy IP resolution (Phase 7 hardening, ADR-0045; corrected
    # 2026-09-21 — the first version trusted only one hop and was reproducing
    # the bug it closed against this repo's real two-hop chain). See
    # app/utils/client_ip.py for the full rationale. `trusted_proxy_hostname`
    # is a COMMA-SEPARATED list of Docker Compose service names, each
    # resolved independently via embedded DNS on every rate-limit / lockout
    # check — default "frontend,cloudflared" (nginx + the tunnel sidecar)
    # matches this compose file's actual topology and needs no operator
    # action. Managed-tenant deployments (a different topology entirely —
    # see docs/managed-hosting.md) MUST override this to "caddy". `
    # forwarded_allow_ips` is an optional additional comma-separated
    # allowlist of literal IPs/CIDRs for non-default topologies or a hop
    # that can't be resolved by hostname from the caller's own Docker
    # network (e.g. a managed tenant trusting the shared gateway's subnet);
    # empty means "trust only the dynamically-resolved proxies plus
    # loopback."
    trusted_proxy_hostname: str = "frontend,cloudflared"
    forwarded_allow_ips: str = ""

    # Operator timezone (ADR-0017). Defines the calendar date of a flight:
    # a flight's stored instant is UTC, but its *date* is the date in this
    # timezone. Flights flown in the evening in the Pacific zone otherwise
    # show the next (UTC) day. Override per deployment via OPERATOR_TIMEZONE.
    operator_timezone: str = "America/Los_Angeles"

    # Customer intake
    frontend_url: str = "http://localhost:3080"
    intake_token_expire_days: int = 7

    # Signed-TOS download link (ADR-0045, Phase 7 hardening). The intake
    # token doubles as the bearer credential for GET
    # /api/tos/signed/by-token/{token} so the customer keeps durable access
    # to their own signed copy after the (much shorter) intake window
    # closes — but "durable" previously meant literally unbounded: a token
    # that ever leaked (shared inbox, proxy log, forwarded email) remained a
    # valid PII-download credential forever, with no operator remedy. This
    # bounds it generously (default ~2 years) rather than removing the
    # durable-access property outright.
    tos_signed_download_expire_days: int = 730

    # Client portal
    client_token_expire_days: int = 30

    # Stripe (optional — falls back to DB-stored settings)
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_publishable_key: str = ""

    # Managed instance (hosted by BarnardHQ)
    managed_instance: bool = False
    client_id: str = ""

    # Admin credentials for managed instance auto-provisioning
    admin_username: str = ""
    admin_password: str = ""

    # Demo mode
    demo_mode: bool = False
    demo_reset_interval_hours: int = 24

    # ntfy (ADR-0036 — replaces Pushover transport for ADR-0002 §5
    # silent-drift watchdog + ADR-0003 zero-touch key rotation alerts).
    # Optional: if the publisher token is set, alerts publish to the
    # self-hosted ntfy at ntfy.barnardhq.com (with publisher-side
    # fallback to ntfy.sh on a per-service obscured topic). Unset =
    # no-op (watchdog still logs to structured JSON, alerts just
    # don't go out). Watchdog contract from ADR-0002 §5 + ADR-0003 is
    # preserved unchanged — only the transport switched.
    ntfy_droneops_publisher_token: str = ""
    # Silence threshold — a device key used inside activity_window_days
    # that has NOT been seen in silence_hours triggers an alert.
    device_silence_activity_window_days: int = 7
    device_silence_hours: int = 48
    # Per-key dedup cooldown to avoid alert spam on a long outage.
    device_silence_dedup_hours: int = 12

    # Google review prompt. Surfaces on the final-invoice PDF, the
    # report-delivery email, the post-payment success state in the
    # client portal, the payment-received email, and the report-ready
    # email. Override per deployment via GOOGLE_REVIEW_URL; unset =
    # CTAs hide themselves (templates gate on truthiness).
    google_review_url: str = "https://g.page/r/Cbblmcdaz3GfEBM/review"

    # Cloudflare Access SSO for the OPERATOR surface only (ADR-0047; under
    # noc-master ADR-0246 decision 2, fleet-sso-conversion-roadmap Phase
    # 4.4). The customer-facing client portal / intake / TOS routes never
    # read these — they stay app-local permanently (ADR-0246 decision 5).
    #
    # Verification requires BOTH of the next two to be non-empty (mirrors
    # the marketing pilot's structural gate, ADR-0099) — the Access app for
    # droneops.barnardhq.com already exists at the edge, but until an
    # operator sets both of these on the running container the CF-Access
    # branch of get_current_user() never runs, jose/httpx never makes a
    # network call, and this ships as a no-op for every request. Empty by
    # default so a self-hosted / OSS install (which has no Cloudflare
    # Access at all) and the public demo instance are entirely unaffected.
    cf_access_team_domain: str = ""
    cf_access_aud: str = ""
    # Comma-separated allow-list — defence in depth, independent of
    # Cloudflare's own Access policy. Defaults to the canonical BarnardHQ
    # operator identity (ADR-0055) when unset.
    cf_access_allowed_emails: str = ""

    # Step B kill switch (ADR-0047) — OFF by default everywhere, including
    # BarnardHQ's own production compose file. Only an operator flipping
    # this explicitly, after CF Access verification has been proven live
    # (roadmap Phase 4.4 Step A soak), disables local username/password
    # login. Self-hosted/OSS installs and the public demo instance
    # (docker-compose.demo.yml, no Cloudflare Access) never set it, so
    # they keep local login as their only auth path, unchanged. See
    # app/routers/auth.py.
    local_login_disabled: bool = False

    @property
    def database_url_sync(self) -> str:
        """Synchronous database URL for Celery tasks."""
        return self.database_url.replace("+asyncpg", "")

    class Config:
        env_file = ".env"


settings = Settings()
