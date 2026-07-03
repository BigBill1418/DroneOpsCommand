# Plan: Client-Report Narrative Quality Pass (FU-AI-QUALITY-PASS)

- **Date:** 2026-07-03
- **Status:** Proposed (concrete options for the parked "it's ok for now"
  watching brief)
- **Owner:** engineering (DroneOpsCommand)
- **Related:** ROADMAP `FU-AI-QUALITY-PASS`; ADR-0015 (single client-facing
  audience contract); **ADR-0029 (altitude / Part-107 guard — MUST NOT be
  weakened)**; ADR-0030 (4096-token output caps); global rule: reports must never
  flag altitude/Part-107 exceedance.
- **Airdata:** ignored entirely — Bill will never use it.

---

## 1. What exists today (verified)

- **One shared system prompt**, `SYSTEM_PROMPT_TEMPLATE`, defined in
  `backend/app/services/ollama.py:9-67` and **imported** by
  `backend/app/services/claude_llm.py:6` — a single edit point for both the
  Claude and Ollama providers (Ollama is the local fallback; managed instances
  force Claude). The user-prompt template is duplicated in each provider
  (`claude_llm.py:51-67`, `ollama.py:104-120`) but is textually identical.
- **One prompt generates the whole report** (5 mandated sections: Mission
  Overview, Area Coverage, Flight Operations Summary, Key Findings, optional
  Client Follow-Up Items). No per-section or per-mission-type prompting.
- **Voice:** operator-to-client, strict third person ("the operator" /
  company name), never addresses or coaches the pilot (ADR-0015).
- **Caps:** Claude `max_tokens=4096` (temperature omitted by design — current
  Claude models 400 on it); Ollama `num_predict=4096`, `num_ctx=8192`, `temp=0.3`
  (ADR-0030).
- **Guards are two-layer:** the prompt's ALTITUDE prohibitions **plus** a runtime
  detector `backend/app/services/report_audience.py` that flags the report
  (`Report.has_audience_leak`) if limit/Part-107/coaching language reappears.
- **Data fed to the model** (`reports.py` context assembly): mission title/date/
  type/location, authoritative mission totals, per-flight `Aircraft` + preformatted
  `Max Altitude` + `Notes`, and the operator's freeform narrative as "CONTEXT
  ONLY."
- **PDF frame** (`report_pdf.html:469`): the narrative is injected as
  `report.final_content` between rich non-LLM sections (stats box, Aircraft
  Deployed cards, flight-path map, imagery grid, footage download, invoice).

**Operator signal (verbatim, 2026-05-14):** *"its ok for now but it needs to get
better."* No specific direction — this plan proposes concrete, guard-safe levers,
not a mandate to grind them all.

## 2. Hard boundaries (do not cross)

Every proposal below operates **inside** these contracts:

- **ADR-0029:** the narrative must NEVER announce, flag, list, compute, rank, or
  comment on any altitude limit / the 400 ft AGL ceiling / any Part-107 or
  regulatory limit. Altitude is neutral capture data only. The single permitted
  compliance line is the positive "conducted in accordance with FAA Part 107
  procedures." **No proposal here adds altitude judgment or per-flight altitude
  ranking.**
- **ADR-0015:** one audience — the client. No pilot coaching, no second person to
  the operator, no operator self-critique.
- **ADR-0030:** stay within 4096 output tokens / 8192 Ollama context.
- The runtime `report_audience.py` detector stays in force; any prompt change is
  validated against it (a change that increases leaks is rejected).

## 3. Concrete improvements (specific, ordered by value/risk)

### 3.1 Kill hedging language (highest value, lowest risk)
The current prompt says "be professional, concise, factual" but does not forbid
weak verbs. Client deliverables read as tentative when the model writes "appeared
to," "seemed," "was observed to," "likely." Add an explicit clause:

> "Write with definitive, factual authority. State what the operation
> accomplished and what the data shows in the active voice. Do NOT hedge with
> 'appeared to', 'seemed', 'was observed to', 'likely', or similar softeners when
> the flight data or operator notes support a direct statement. Only qualify a
> statement when the underlying data is genuinely uncertain."

Risk: none re: guards (it is a voice tightening). Validate the golden-report diff
still passes the audience detector.

### 3.2 Signal-density / anti-bloat budget
The prompt asks for "concise" but gives no length discipline, and drafts trend
toward narrative bulk (ROADMAP candidate area). Add a per-section length budget
and a "no filler" rule:

> "Each section is 2–5 sentences of substance. Do not pad, restate the section
> heading as a sentence, or add generic aerial-operations boilerplate. Prefer one
> precise sentence with a specific number over three general ones. If a section
> has little to report for a routine flight, keep it short — brevity is
> professional, not a defect."

This directly addresses "conciseness" and "signal-to-noise on routine flights."

### 3.3 Structured specificity — make the model *use the numbers*
The user prompt hands the model exact totals and per-flight data but only says
"use specific numbers." Strengthen it to require the concrete figures in the
right sections:

> "Ground every claim in the provided figures. Flight Operations Summary must
> state the total flight count, total flight time, total distance, and the
> specific aircraft used, each with its provided unit. Area Coverage must state
> the acreage/area figure when provided. Never write a vague quantity ('several
> flights', 'a large area') when an exact number is available."

Guard-safe: altitude stays neutral (already preformatted "N m AGL (N ft)"; the
prompt continues to forbid any limit commentary).

### 3.4 Routine-flight variant (conditional structure)
When nothing notable happened, five full sections manufacture noise. Rather than
a second prompt (more maintenance), add a conditional instruction:

> "When the mission is routine and the data shows no notable findings, produce a
> tight report: a substantive Mission Overview, a factual Flight Operations
> Summary and Area Coverage, and a Key Findings section that plainly states the
> objective was met and the deliverables captured. Omit Client Follow-Up Items
> unless a genuine client action exists. Do not invent findings to fill space."

This keeps one prompt while letting the report right-size itself.

### 3.5 Section-5 framing strength
"Client Follow-Up Items" already has a strong OMIT-fallback (ADR-0015). The only
safe iteration is sharpening *what qualifies*: frame it explicitly as
client-actionable outcomes tied to the imagery ("the flagged area in the
north-east corner warrants on-the-ground inspection by the property owner"),
never operator/technique notes. Keep the existing forbidden-phrase list.

### 3.6 Voice calibration for the growth lines (optional, per-tenant-ready)
The voice is correct (operator-to-client, factual). For the **real-estate /
marketing** line, a slightly more outcome/deliverable-oriented register reads
better to a listing agent than a pure inspection log — e.g. leading Key Findings
with what was captured for the client's use. This overlaps with the per-tenant
prompt-fragment infrastructure noted in ROADMAP (FU-AI-4); if that lands, a
per-mission-type fragment is the clean home for this, rather than branching the
shared prompt now. **Do not** drift toward marketing hype — factual and specific
still wins; this is register, not adjectives.

## 4. Template-side (non-prompt) improvements

- The narrative injects as `{{ report.final_content | safe }}` in a single
  `narrative` div. If §3.2's per-section discipline lands, consider having the
  model emit lightweight section headings the template can style consistently
  (it already implies headed sections) — a small typographic win, no guard
  impact.
- Nothing else in `report_pdf.html` needs to change for quality; the surrounding
  data sections (stats, aircraft cards, map, imagery) are already strong.

## 5. How to ship safely

1. **All prompt edits are one file** (`SYSTEM_PROMPT_TEMPLATE` in `ollama.py`) —
   both providers inherit them.
2. **Golden-report diff harness** (shared with the flight-attach plan): snapshot
   narratives for a fixed mission set (routine survey, rich-findings inspection,
   real-estate shoot, multi-aircraft) before/after each prompt change; a human
   reads the diff for quality; the `report_audience.py` detector must show **zero
   new leaks**.
3. **Ship 3.1 + 3.2 + 3.3 together as one low-risk pass** (voice authority +
   anti-bloat + number-grounding) — these are the highest-value, guard-neutral
   levers and directly answer "it needs to get better." Treat 3.4/3.5/3.6 as
   follow-ups gated on Bill's reaction to the first pass.
4. Version bump per CLAUDE.md; manual BOS-HQ rebuild; ADR-0030 caps unchanged.

## 6. Explicitly out of scope

- Any altitude/Part-107 exceedance commentary (ADR-0029) — untouched.
- Re-introducing a second audience or pilot coaching (ADR-0015) — untouched.
- Airdata — ignored entirely.
- Raising token caps (ADR-0030 4096 is sufficient; bloat is the problem, not
  truncation).

## 7. Decision for Bill

Approve a **single low-risk prompt pass (3.1 voice authority + 3.2 anti-bloat +
3.3 number-grounding)**, validated by the golden-report diff + the existing
audience-leak detector, and react to the output before deciding on the routine-
flight variant and the real-estate register work?
