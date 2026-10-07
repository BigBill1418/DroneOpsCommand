# ADR-0051: ntfy header values are RFC 2047-encoded when non-ASCII

**Status:** Accepted — v2.97.3 (2026-10-06)
**Amends:** the ntfy helper from ADR-0036 migration (`app/services/ntfy.py`, v2.63.12, 2026-04-26)

## Context: the defect

On 2026-10-06, arming the basemap tile-health probe (ROADMAP MP-2) included a re-fire of the
alert path through the production helper. The test alert was **dropped**. The worker logged:

`ntfy primary exception: 'ascii' codec can't encode character '—'` (the fallback failed
the same way), followed by `alert dropped`.

httpx encodes header values as ASCII. Any title or tag with a non-ASCII character, such as the
em dash every alert here uses, therefore raised **before the request was sent**, on the primary
publish and on the ntfy.sh fallback alike. The helper caught the exception and logged it, so
nothing ever surfaced as an error.

Affected titles on `main` (2026-10-06):

| Alert | Title | Effect |
|-------|-------|--------|
| Device-silence watchdog | `DroneOps — <device> silent for Nh` | **Never delivered.** Loki shows 86 drop lines in the worker over the last 29 days. They concerned M30T and M4TD, which have since resumed uploading. |
| Stripe deposit received | `[DroneOps Command] Deposit received — '<mission>' — $N` | Never delivered |
| Stripe balance paid | `[DroneOps Command] Balance paid — '<mission>' — $N` | Never delivered |
| Stale device key | `DroneOps — stale device key attempt from <ip>` | Never delivered |
| Basemap probe (MP-2) | `Basemap tiles changed — <layers>` | Would never have delivered once armed |

Message **bodies** were never affected, because the body is not a header.

## Decision

`_build_headers` passes `Title` and `Tags` through `_header_safe()`. An ASCII value is sent
unchanged. A non-ASCII value is sent as an RFC 2047 encoded-word, `=?UTF-8?B?<base64>?=`.

This was verified against the live server before shipping. A publish with an encoded em-dash
title to `ntfy.barnardhq.com/droneops-alerts` was read back with the title decoded correctly.

## Alternatives considered

- **Strip or replace non-ASCII characters.** Rejected: it loses information and every caller
  would need to remember to do it.
- **Publish as a JSON body** (`POST /` with `{"topic", "title", ...}`). That would also work,
  but it changes the request shape of both the primary and the fallback publish paths. The
  encoded-word fix is local to header construction.

## Consequences

- The device-silence, Stripe payment and stale-device-key alerts start arriving on
  `droneops-alerts` for the first time since 2026-04-26.
- Other fleet helpers (Node `notifications.js`, `ntfy-publish.sh`) have their own header code and
  were not checked by this ADR. Their em-dash titles have been observed arriving, so they are
  assumed to be fine. Verify before relying on that.
- Failover: there is no state change and the helper's request shape is unchanged.

## Amendment 1 — 2026-10-06: DOC's shell-script alerts checked

DOC's `scripts/droneops-backup.sh`, `restore-drill.sh`, `droneops-backup-cutover.sh` and
`demo-nightly-reset.sh` publish through the host helper `~/.local/bin/ntfy-publish.sh`
(`curl -H "Title: …"`), not through `app/services/ntfy.py`. They are **not affected**:
- Every title they pass is plain ASCII (backup FAILED, restore drill OK/FAILED, cutover
  ABORTED/complete, demo reset FAILED). The cutover em dash is in the body, not a header.
- curl sends a raw UTF-8 `Title:` header and ntfy decodes it. A probe from BOS-HQ to an
  unsubscribed scratch topic read back `[DroneOps Command] probe — raw UTF-8 title` intact.
  The failure in this ADR is specific to httpx's ASCII header encoding.

Helix-Hub fixed the same httpx bug independently in its ADR-0200 (#182/#184).

