# ADR-0035 — Client-report narrative quality: authority, signal-density, number-grounding levers

- **Status:** Accepted
- **Date:** 2026-07-03
- **Version:** v2.77.0
- **Related:** ADR-0015 (single client-facing audience contract), **ADR-0029
  (altitude / Part-107 guard — NOT weakened)**, ADR-0030 (4096-token output
  caps — unchanged). Implements the top three levers of
  `docs/plans/2026-07-03-report-quality.md` (ROADMAP `FU-AI-QUALITY-PASS`).

## Context

The operator's standing signal on the generated client report (verbatim,
2026-05-14): *"its ok for now but it needs to get better."* The shared system
prompt (`SYSTEM_PROMPT_TEMPLATE` in `backend/app/services/ollama.py`, imported by
`claude_llm.py` — one edit point for both providers) said "be professional,
concise, factual" but gave the model no discipline on **voice authority**,
**length/bloat**, or **using the concrete figures it is handed**. Drafts read
tentatively ("appeared to", "was observed to"), padded routine flights into five
full sections of boilerplate, and hedged quantities ("several flights") when an
exact number was in the context.

## Decision

Ship the three highest-value, lowest-risk levers from the plan doc (§3.1–3.3) as
one guard-neutral pass to the **shared** prompt. Each operates strictly inside
the ADR-0015 / ADR-0029 / ADR-0030 boundaries:

1. **Kill hedging (§3.1).** Require definitive, active-voice authority; forbid
   "appeared to", "seemed", "was observed to", "it is likely" and similar
   softeners unless the underlying data is genuinely uncertain.
2. **Anti-bloat / signal-density budget (§3.2).** Each section is 2–5 sentences
   of substance; no padding, no restating the heading, no generic
   aerial-operations boilerplate; brevity on a routine flight is professional,
   not a defect.
3. **Number-grounding (§3.3).** Ground every claim in the provided figures — the
   Flight Operations Summary must state flight count, total flight time, total
   distance, and the specific aircraft with units as given; Area Coverage must
   state the acreage when provided; never a vague quantity when an exact number
   exists.

## Guard safety (ADR-0029)

The number-grounding lever is the only one that touches "cite the numbers," so it
carries an **explicit altitude carve-out**: number-grounding does NOT extend to
altitude — altitude stays neutral capture data, and the prompt continues to
forbid ranking, singling out, tallying, or commenting on flights by altitude.
This *reinforces* rather than relaxes the ADR-0029 prohibitions. Validation:

- The runtime detector `backend/app/services/report_audience.py` is unchanged.
- `backend/tests/services/test_report_audience_guard.py` gains
  `TestNarrativeQualityLevers`, which locks the three levers structurally,
  asserts the altitude carve-out text is present, and proves a representative
  full report **containing a flight at 146.3 m AGL (480 ft)** stays guard-clean
  (zero detector leaks).
- The full ADR-0029 altitude-guard and audience-leak suites still pass unchanged.

## Consequences

- Both provider paths (Claude live/managed, Ollama fallback) inherit the levers
  from the single template — no per-provider drift.
- ADR-0030 caps are untouched; the prompt grew ~200 words of instruction, well
  within the 8192-token Ollama context / 200k Claude context.
- Levers §3.4 (routine-flight variant), §3.5 (Section-5 framing), §3.6
  (real-estate register) remain follow-ups, gated on the operator's reaction to
  this pass, per the plan doc.
- This repo is `.deployer-disabled`; the change is hand-deployed on BOS-HQ
  (`docker compose build backend worker beat && up -d --no-deps …`). Verify by
  the public OpenAPI version (2.77.0), not `deployer-state.json`.
