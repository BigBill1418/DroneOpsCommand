# Incident: Mission-report audience leak — 2026-05-14

**Author:** Terry (research / documentation lane; aegis owns the code-level RCA + fix)
**Status:** Open — under investigation. Quality defect, not an outage. No customer impact (operator caught the defect on his own personal instance before the report shipped). Use "close-out thoroughness" workflow per operator preference, not active-incident hotfix.
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

## 9. Open questions (require aegis or operator follow-up)

1. **(operator)** What does `SELECT value FROM system_settings WHERE key='llm_provider';` return on the personal instance? Confirms whether Claude or Ollama is currently in the loop.
2. **(aegis)** Did the actual offending generation use Claude or Ollama? If logs from `logger.info("LLM provider resolved to '%s'", provider)` are still in Loki for 2026-05-14, that answers Q1 for the specific run.
3. **(aegis)** Is the defect reproducible with `temperature=0` on both providers? If yes, it is fully prompt-driven; if no, temperature contributes and the fix should also drop temperature to e.g. `0.1` for the client-facing path.
4. **(operator)** Should the prompt fix ship as a backport to the managed-hosting tenants immediately, or wait for one self-hosted soak cycle on the personal instance? (Default recommendation: 24h soak on personal, then push to managed.)
