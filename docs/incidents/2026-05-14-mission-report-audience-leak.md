# Incident: Mission-report audience leak — 2026-05-14

**Author:** Terry (research / documentation lane; aegis owns the code-level RCA + fix)
**Status:** Closed (RCA + prompt fix) / In-progress (runtime soft-block gate). Quality defect, not an outage. No customer impact (operator caught the defect on his own personal instance before the report shipped). "Close-out thoroughness" workflow per operator preference, not active-incident hotfix. See §10 for the post-RCA decisions.
**Severity:** Class-Q (quality defect in a generated, client-facing artifact). Below ntfy alerting threshold under ADR-0037. No publish.

## 1. Symptom

Operator (Bill, on the personal instance at `droneops.barnardhq.com`) generated a final mission report on 2026-05-14 and observed that the *client-facing* report contained recommendations directed at **him**, telling him how to act and how to fly the mission. The report is supposed to be the inverse: take *his* operator notes as input and produce a *client-facing* after-action account of the mission as flown, written for the customer who paid for the flight.

Reported by the operator verbatim:

> "The final client report contains recommendations for how I should act and fly my mission. That is wrong. The report's purpose is to take MY input and generate a final report for the client."

## 2. Scope

- **Affected surface:** LLM-generated mission report `final_content`, rendered into the customer-facing PDF via `backend/app/services/pdf_generator.py` and the `MissionReportEdit` facet (`/missions/:id/report/edit`).
- **Affected backends:** Both LLM provider paths — Claude API (`claude_llm.py`) and Ollama (`ollama.py`) — share the same `SYSTEM_PROMPT_TEMPLATE` (Claude imports it from `ollama.py` at `claude_llm.py:6`). The defect is therefore prompt-level and provider-agnostic.
- **Affected instances:**
  - Personal instance (`droneops.barnardhq.com`, this codebase, current `main`) — confirmed by operator report.
  - Demo instance (`command-demo.barnardhq.com`) — same code path, same prompt template, latent.
  - Managed-hosting customers (if any are live on this template) — same code path, same prompt template, latent.
- **Affected versions:** Any version that includes the current `SYSTEM_PROMPT_TEMPLATE` in `backend/app/services/ollama.py`. The template has been in place since the LLM dispatch system was introduced — this is not a regression from a recent change.

## 3. Impact

- **Customer impact:** None observed. The operator reviews every report before it goes out via the `MissionReportEdit` facet, and caught the defect during review. The `MissionReportEdit` UI is the editorial gate; the LLM output is a draft, not the final shipped artifact.
- **Operator impact:** Every generated report requires manual cleanup of audience-misaligned content. Editorial burden, not data loss.
- **Trust impact:** The defect erodes the value of the AI-assist surface. The prompt is not doing the job it claims to do ("client-facing"), so the operator cannot trust the draft as a starting point.

## 4. AI backend confirmation (the operator's first question)

**Operator's belief:** the personal instance uses the Claude API for AI generation of the mission report.

**Code-path resolution (verified 2026-05-14):**

The dispatcher at `backend/app/services/llm_provider.py:23-36` resolves the provider in this order:

1. If `settings.managed_instance` is true → forces `"claude"` regardless of DB.
2. Else, read `system_settings.llm_provider` from the database. If `"claude"` or `"ollama"`, use that.
3. Else, fall back to `settings.llm_provider` from config (env-default `"ollama"`).

**Personal instance configuration (verified from `/home/bbarnard065/droneops/.env`):**

- `MANAGED_INSTANCE` — **not set** in `.env`. Defaults to `False` per `backend/app/config.py:60` (`managed_instance: bool = False`).
- `LLM_PROVIDER` — **not set** in `.env`. Defaults to `"ollama"` per `backend/app/config.py:19`.
- `OLLAMA_BASE_URL=http://ollama:11434` — present.
- `OLLAMA_MODEL=llama3.1:8b-instruct-q4_K_M` — present.
- `ANTHROPIC_API_KEY` — **not set** as an env var.

So whether the personal instance is actually running on Claude depends on **one DB value**: `system_settings.llm_provider`. If the operator (or a prior session) set that to `"claude"` and stored an `anthropic_api_key` via the Settings UI (`PUT /api/settings/llm`, `backend/app/routers/system_settings.py:615`), then Claude is in use. Otherwise the personal instance is on Ollama (llama3.1:8b-instruct-q4_K_M).

**Code-level confirmation that Claude API is wired and works:**

- File: `backend/app/services/claude_llm.py`
- Library: `anthropic` (the official Python SDK), imported at `claude_llm.py:3`.
- Model: `claude-sonnet-4-20250514` (hard-coded at `claude_llm.py:10` as `MODEL = "claude-sonnet-4-20250514"`).
- API call: `client.messages.create(...)` at `claude_llm.py:72-78` with `temperature=0.3`, `max_tokens=1024`, and the `SYSTEM_PROMPT_TEMPLATE` from `ollama.py:9-21`.
- Auth: API key resolved from the DB-stored `anthropic_api_key` setting first (`llm_provider.py:73`), falling back to `settings.anthropic_api_key` from env. Personal-instance env has no key, so Claude path requires the DB to hold one.

**No other LLM providers are configured.** Repo-wide grep for `anthropic|claude|openai|gpt|llm_` returns only the two intended providers (Claude + Ollama). There is no OpenAI client, no Gemini client, no Bedrock client, no local Llama.cpp client. The dispatcher is binary: Claude or Ollama.

**Net answer for the operator:**

> Claude API is wired and functional. Model is `claude-sonnet-4-20250514`. Whether the *personal instance* is actually using it on 2026-05-14 depends on the DB row in `system_settings` table (`key='llm_provider'`). Operator can verify in two ways: (a) open the Settings page in the UI and look at the "AI / Report Generation" panel — the active provider is shown there; or (b) run `docker compose exec db psql -U droneops -c "SELECT key, value FROM system_settings WHERE key IN ('llm_provider','anthropic_api_key');"`. If the answer is `claude`, the operator's belief is correct; if it is unset or `ollama`, the personal instance has been on the local llama3.1:8b-instruct-q4_K_M model the whole time.

**Either way, the prompt defect described below is shared between both backends and is the actual root cause of the audience leak.** Switching backends will not fix it.

## 5. Root cause (preliminary — aegis owns the definitive RCA)

The shared `SYSTEM_PROMPT_TEMPLATE` lives at `backend/app/services/ollama.py:9-21` and is imported by Claude at `claude_llm.py:6`. Excerpt:

```
You are a professional drone operations report writer for {company_name}, an FAA Part 107
certified drone operations company. Generate a detailed, client-facing after-action report
based on the following mission data and operator notes.

Include these sections:
1. **Mission Overview** ...
2. **Area Coverage** ...
3. **Flight Operations Summary** ...
4. **Key Findings** ...
5. **Recommendations** - Follow-up actions or suggestions for the client
...
```

Three observations:

1. **The prompt declares the audience is "the client" but never names who the client is, never names that the operator is a distinct party, and never forbids the LLM from addressing the operator.** A model with the operator's narrative in front of it, prompted for "Recommendations," will routinely write recommendations *to whoever is closest in the conversation context*. That is the operator, because the operator's notes are the freshest natural-language signal in the user prompt.

2. **Section 5 ("Recommendations") is structurally inviting the leak.** "Follow-up actions or suggestions for the client" is a valid section for a client-facing artifact (e.g., "schedule a re-flight after the next rainfall," "review the marked anomalies on page 3 with your contractor"), but the instruction "Follow-up actions" without an explicit audience pin lets the model drift into operator coaching — "next time, consider flying earlier in the day for better light" — which is exactly what the operator reports seeing.

3. **The user-prompt block ends with "Operator Notes:\n{user_narrative}\n\nGenerate the after-action report:"** (`ollama.py:69-72`, mirrored at `claude_llm.py:63-66`). The labeled `Operator Notes` section is the input; the report is supposed to be the output. Nothing in the prompt explicitly tells the LLM that the operator is not the reader of the report. With temperature 0.3 the model still has enough latitude to mirror the operator's voice/perspective back into the output.

**Conclusion (Terry's read, pending aegis confirmation):** the prompt template fails to separate the *author/source* (operator) from the *audience* (client). It implies "client-facing" but the structural cue from Section 5 plus the labeled operator narrative create a strong pull toward operator-coaching output. The defect is in the prompt, not in either provider's API call, and not in the editorial UI.

> **Root cause: pending — see aegis findings.** Aegis is doing the code-level RCA and the fix in parallel. If aegis identifies a different or additional root cause (e.g., something in the report-generation router that injects operator context inadvertently, or a temperature/model-tuning issue), update this section before publishing.

## 6. Fix plan (preliminary — aegis owns the implementation)

Pending aegis's definitive RCA, the expected shape of the fix is a prompt-template rewrite that:

1. **Names the audience explicitly.** The reader is the paying customer (use `{customer_name}` if available; otherwise "the customer who commissioned this mission"). The operator is the *author of the input notes*, not the reader.
2. **Names the operator explicitly as the author of the input.** "The following are the operator's contemporaneous mission notes. Use them as source material; do not address the operator in your output."
3. **Reframes Section 5.** Either:
   - **Option A:** Rename "Recommendations" to "Recommended follow-up for the customer" and require every bullet to be an action the *customer* (not the operator) can take.
   - **Option B:** Drop Section 5 from the client-facing template entirely and move operator-facing coaching to a separate, opt-in section that the operator alone sees in `MissionReportEdit`. (This is the ADR-0015 direction.)
4. **Adds a negative instruction.** "Do not include flight-technique advice, operator coaching, or suggestions for the operator's future missions. The customer has not asked for your opinion on the operator's flying."
5. **Verifies on both providers.** The same prompt change must be exercised against both Claude (`claude-sonnet-4-20250514`) and Ollama (`llama3.1:8b-instruct-q4_K_M`) before claiming the fix. Llama 3.1 8B is more susceptible to audience drift than Claude Sonnet 4; if the prompt passes on llama3.1:8b it will almost certainly pass on Claude.

**Backstop control (independent of the prompt fix):** the `MissionReportEdit` facet UI (`frontend/src/pages/MissionReportEdit.tsx`) is the editorial gate. The operator always reviews and can rewrite before send. This caught the current defect. The prompt fix raises the floor on draft quality; the editorial gate stays as the ceiling.

> **Implementation status: pending — aegis owns the patch.** Will be linked here once landed (commit SHA + version bump + CHANGELOG entry).

## 7. Prevention

1. **ADR-0015** (parallel to this incident doc) records the durable design principle: *mission reports are client-facing artifacts; operator-facing coaching is a separate surface.* This is the rule the prompt has to enforce going forward, and the rule that any future "AI assist" surface in the product must honor.
2. **Prompt regression fixture.** Add a fixture to `backend/tests/` that submits a representative mission to the LLM dispatcher with a deliberately operator-voiced narrative (e.g., notes containing "I had trouble seeing the drone in glare — next time I should bring polarized glasses"), and asserts the output (a) does not contain a second-person address to the operator, (b) does not echo operator coaching back as "recommendations." Use a deterministic provider (Ollama with `temperature=0` + a small model) so the test is stable in CI. This is a `pytest` mark on `tests/test_llm_report_audience.py` (aegis to file or refile).
3. **Prompt-source-of-truth pin.** Move `SYSTEM_PROMPT_TEMPLATE` out of `ollama.py` (where it is incidentally located because Ollama was the first provider) into `backend/app/services/llm_prompts.py` or a sibling. Import from there in both `claude_llm.py` and `ollama.py`. The prompt is the contract; it should not live in a provider module.
4. **Audience separation in the data model.** If/when ADR-0015 lands a separate operator-facing surface, the `Report` model gains a second field (e.g., `operator_notes_final`) that is *never* included in the customer PDF. The customer-facing `final_content` field stays exactly as today. Schema change is small (one nullable text column); migration is reversible.

## 8. References

- `backend/app/services/llm_provider.py` — dispatcher, lines 23-78
- `backend/app/services/claude_llm.py` — Claude path, model pin at line 10
- `backend/app/services/ollama.py` — Ollama path + `SYSTEM_PROMPT_TEMPLATE` at lines 9-21
- `backend/app/routers/reports.py` — generation endpoint, lines 91-191
- `backend/app/routers/system_settings.py` — LLM settings router, lines 574-643
- `backend/app/tasks/celery_tasks.py:172-182` — async dispatch from Celery
- `frontend/src/pages/MissionReportEdit.tsx` — editorial gate
- `docs/adr/0015-mission-report-audience-separation.md` — durable decision (this incident's ADR)
- ADR-0037 §5-question gate — confirms this defect is below ntfy publish threshold (no customer impact, no imminent service degradation, no actionable 5-min window — dashboard / log only)
- Operator preference: `~/.claude/projects/.../feedback_close_out_thoroughness.md` — planned-work close-out style applies; no hotfix shortcut.

## 9. Open questions — close-out status (2026-05-14 PM)

1. **(Q1 — root cause confirmation) — CLOSED.** Aegis confirmed the
   preliminary prompt-level RCA and committed the fix at `22469ed`
   (`fix(reports): mission-report audience leak — client report no
   longer addresses the operator (ADR-0015)`). System prompt rewritten
   in `backend/app/services/ollama.py:9-38` to name the CLIENT as
   reader, name the operator as upstream author (not reader), forbid
   second-person address to the pilot, call out four common leak
   phrases as FORBIDDEN, and reframe Section 5 to "Client Follow-Up
   Items" with an OMIT-fallback when no client action is warranted.
   User-prompt block in both providers (`ollama.py:75-89` +
   `claude_llm.py:52-66`) now labels operator notes `CONTEXT ONLY` and
   instructs translation into third-person narrative. Plus a 17-test
   regression suite at `backend/tests/services/test_report_audience_guard.py`
   (all green) and a deterministic detector module
   `backend/app/services/report_audience.py` (exposed as a callable for
   the runtime gate — see §10).

2. **(Q2 — DB provider value) — OPEN, OPERATOR ACTION.** Operator to
   verify which provider was actually in the loop for the offending
   run, either via the Settings UI ("AI / Report Generation" panel)
   or:
   ```
   docker compose exec db psql -U droneops -c "SELECT key, value FROM system_settings WHERE key IN ('llm_provider','anthropic_api_key');"
   ```
   Not blocking — the prompt fix is provider-agnostic by construction
   (both Claude and Ollama import the same `SYSTEM_PROMPT_TEMPLATE`).
   Answer is for completeness of the incident record.

3. **(Q3 — Loki log evidence) — OPEN, OPERATOR ACTION.** Loki should
   carry the `LLM provider resolved to '%s'` INFO line from
   `llm_provider.py:54` for 2026-05-14, which would pin Q2 to the
   specific run. Operator to grep when convenient; same not-blocking
   status as Q2.

4. **(Q4 — temperature contribution) — DEFERRED, LOW PRIORITY.**
   Aegis's patch did not address temperature; the prompt rewrite alone
   appears sufficient (17/17 regression tests passing, Layer 1
   structural assertions + Layer 2 detector exercising the verbatim
   shape of the operator-reported leak). If the soft-block gate
   (§10) ever flags a real-world draft in production, that is the
   moment to revisit whether temperature also needs to drop to e.g.
   `0.1` on the client-facing path. Until then, no action.

5. **(Q5 — managed-tenant rollout cadence) — DECIDED.** Default of
   24h personal-instance soak before propagating to managed tenants is
   accepted (operator's standing preference for thoroughness over
   speed on planned non-incident work).

## 10. Decisions made post-RCA (2026-05-14 PM)

After aegis's prompt fix at commit `22469ed`, the operator made two
close-out decisions that resolve the open architectural questions
this incident raised and supersede the earlier "Proposed" framing of
ADR-0015.

### Decision A — Operator-facing debrief surface is DROPPED (not deferred)

Operator quote at close-out:

> "drop it — i don't need a operator debrief — never asked for that."

The earlier framing — "client surface today, operator debrief
tomorrow" — assumed there was a legitimate future home for the
operator-coaching prose that leaked. That assumption was wrong. The
operator never requested AI-generated self-coaching; it was
Terry-supplied scaffolding.

**Result:** ADR-0015 rewritten from Proposed → Accepted with the
stronger decision: *this system produces client-facing reports only.*
No second LLM call, no second `Report` column, no parallel
"retrospective" facet. If operator self-review is ever wanted, it is
a different product decision evaluated on its own merits.

ROADMAP item FU-AI-1 (operator retrospective surface) is **removed
from the roadmap**, not deferred. See ADR-0015 §"Rejected
alternative" for the full reasoning.

### Decision B — Runtime audience-leak soft-block gate is APPROVED

Aegis's commit `22469ed` shipped the detector module
(`backend/app/services/report_audience.py`) as a stable callable
specifically to enable a runtime gate without re-implementing the
rules. The operator approved wiring it as a **soft-block** gate on
every generated report draft:

- Every LLM-produced draft is passed through `has_audience_leak()`
  before reaching the editorial UI.
- On leak detection: the draft is flagged (not silently passed
  through); offending phrasings are surfaced in the
  `MissionReportEdit` banner.
- Operator override is allowed — soft-block, not hard-block. The
  editorial human-in-the-loop gate stays load-bearing.
- The runtime gate is a tripwire on top of the corrected prompt, not
  a substitute for it.

**Shipped (local) at commit `4953edf` (2026-05-14).** Wire-in lives at
the persistence site (`backend/app/tasks/celery_tasks.py:150-189`,
helper `_apply_audience_findings`) so both Claude and Ollama paths are
covered by a single insertion. Two new `Report` columns
(`has_audience_leak BOOL`, `audience_leak_details JSONB`) added via the
existing idempotent `_add_missing_columns` migration path
(`backend/app/main.py:114-122`); failover-safe per CLAUDE.md §Failover
Guard. Helper never raises (detector failure logs and leaves defaults
so generation never 500s on a regex regression). **No regen loop** per
operator directive — detection + surfacing only, with a
doc-string-lock test (`test_no_regen_loop_contract_is_documented`)
preventing drift toward a retry-until-clean pattern. 10 new hermetic
tests in `backend/tests/services/test_audience_leak_persistence.py`
green; existing 17-test audience suite unchanged. ROADMAP item
FU-AI-RUNTIME-GATE flipped IN PROGRESS → SHIPPED; ADR-0015 §Decision-5
updated with the same hash. Deploy is gated on the operator's 24h soak
preference; `.deployer-disabled` keeps SwarmPilot out of the way.

### Related improvement — clear stale draft on Generate click (commit `700c9b0`)

Small UX follow-up landed in the same close-out cycle as the audience
work. Clicking **Generate Report** now clears `reportContent`,
`hasAudienceLeak`, and `audienceLeakDetails` immediately
(`frontend/src/pages/MissionReportEdit.tsx:262-272`) so the operator
gets visual confirmation the new request was accepted rather than
seeing the prior draft sit on screen for the 10–30s generation window.
Conditional render on `reportContent` (line ~625) unmounts the FINAL
REPORT block while empty; the `GENERATING...` button label is the
in-flight signal. PDF + Send naturally disable during the in-flight
window via their existing `!reportContent` guards. Re-entrant clicks
on Generate were already a no-op via `disabled={generating || !narrative}`.
Not an ADR-worthy decision; logged here because it landed alongside
the runtime-gate close-out and addresses the operator-reported "did
it hear me?" confusion during long generations.
