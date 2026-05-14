# ADR-0015: Mission reports are client-facing artifacts; operator-facing coaching is a separate surface

**Status:** Proposed (2026-05-14) — to be Accepted on the same commit that lands the prompt fix.
**Related ADRs:** 0014 (Mission Hub redesign — Hub + Facet pattern), 0013 (contract tests + 4xx burst alerting), 0037 (notification noise reduction policy — confirms this is below ntfy threshold).
**Related incident:** `docs/incidents/2026-05-14-mission-report-audience-leak.md`.
**Author:** Terry (research + docs lane).

---

## Context

On 2026-05-14 the operator (Bill, on the personal instance) generated a final mission report and observed that the *client-facing* report contained recommendations directed at **him**, telling him how to fly future missions. This is a categorical mismatch with the product's intent. The generation pipeline at `backend/app/services/llm_provider.py` accepts operator-authored notes and emits a draft destined for a paying customer; the audience leak inverts that.

The root cause is in the shared `SYSTEM_PROMPT_TEMPLATE` (`backend/app/services/ollama.py:9-21`, also consumed by `claude_llm.py:6`). The template declares the artifact "client-facing" but:

1. Does not name the client explicitly.
2. Does not name the operator as the author of the input rather than a reader of the output.
3. Includes a Section 5 ("Recommendations") with no audience pin, which the LLM resolves toward whoever is closest in the conversation context — that being the operator, since the operator's narrative is the freshest natural-language signal.
4. Has no negative instruction prohibiting operator-coaching prose.

The defect is provider-agnostic (Claude and Ollama share the prompt), so switching backends does not fix it. It is also not a regression — the template has had this shape since the LLM dispatch system was introduced. The defect was masked until now because:

- The operator reviews every report in `MissionReportEdit` before sending. The audience leak required editorial cleanup but did not reach customers.
- On busy mission days the cleanup gets folded into general copy editing and is not flagged as a class of defect.

The operator's verbatim framing of the desired contract:

> "The report's purpose is to take MY input and generate a final report for the client."

That is the contract this ADR enshrines.

## Decision

Adopt the **audience-separation principle** for every LLM-assisted output surface in DroneOpsCommand:

1. **The mission report `final_content` is a client-facing artifact, full stop.** Its audience is the paying customer (the `Customer` row joined to the `Mission`). The LLM prompt names the customer (by name if available, otherwise by role: "the customer who commissioned this mission"). The operator is the *author of the source notes*, not a reader of the report.

2. **Operator-facing coaching, retrospectives, and self-review live on a separate surface.** That surface is *not* the report and *not* the customer PDF. It is a distinct field on the `Report` model (e.g., `operator_retrospective_md`, nullable text, never rendered into `pdf_path`) shown only inside `MissionReportEdit` and only to authenticated operator users. The customer PDF never includes it. The customer email never includes it.

3. **The prompt enforces the separation.** The system prompt explicitly:
   - Names the audience (the customer).
   - Names the operator as the upstream author of the input narrative, not the reader.
   - Forbids second-person address to the operator.
   - Forbids flight-technique advice, operator coaching, gear suggestions, or any prose whose target reader is the operator.
   - Reframes Section 5 "Recommendations" to mean *customer follow-up actions* (e.g., "schedule a re-flight after the next rainfall," "review the marked anomalies on page 3"). If the LLM has nothing to recommend *to the customer*, the section is empty — not filled with operator coaching.

4. **The prompt source-of-truth is a shared module, not a provider module.** Move `SYSTEM_PROMPT_TEMPLATE` from `backend/app/services/ollama.py` to a sibling such as `backend/app/services/llm_prompts.py`. Both `claude_llm.py` and `ollama.py` import from there. The prompt is the contract; it does not belong inside one provider's adapter.

5. **A regression fixture pins the contract.** A test (`backend/tests/test_llm_report_audience.py` or similar) submits a deliberately operator-voiced narrative to the dispatcher and asserts the output contains no second-person address to the operator and no operator-coaching prose. The test uses a deterministic provider configuration (Ollama with `temperature=0` + a small model) to be stable in CI. The Claude path is covered by a separate, optional, network-marked test that the operator can run locally before a managed-tenant push.

6. **Verification gate before any push to managed-hosting tenants.** The prompt fix lands on personal instance first, soaks 24h with at least one real mission report generated and reviewed, then propagates to managed tenants via the existing image rollout. No same-day fan-out, per the operator's preference for thoroughness over speed on planned (non-incident) work.

## Consequences

### What this delivers

- **The mission report is what the operator says it is** — a client-facing account of the mission as flown, written for the customer who paid for the flight. No more audience leak.
- **Operator-facing material has a legitimate home.** If the operator wants the AI to also generate a personal retrospective (what went well, what to do differently next time), that surface exists on its own field, with its own prompt, and never reaches the customer. This is a future feature, not in-scope for the prompt fix, but the ADR pre-clears the architecture.
- **The prompt becomes the contract.** Co-located in `llm_prompts.py`, version-controlled, with a fixture pinning the audience guarantee. Any future change to the prompt is a code review against a known invariant.
- **Provider-swap is now safe.** Today the same template is shared across Claude and Ollama. Tomorrow if a customer's tenant runs a third provider, the audience separation comes with it because it is in the prompt, not in any provider adapter.

### What this costs

- **Operator-retrospective surface is deferred, not delivered.** This ADR scopes only the audience-separation fix. The operator-facing coaching surface is described as the legitimate home for that material, but actually building it is a follow-up (ROADMAP item, to be added in the same commit as this ADR).
- **One small schema migration may follow.** If/when the operator-facing surface ships, the `Report` model gains a nullable text column. That migration is reversible and trivial. No customer data is touched.
- **One additional CI test.** The regression fixture adds a few seconds to the backend test suite. Acceptable.
- **Prompt module relocation is a low-risk refactor.** Moving `SYSTEM_PROMPT_TEMPLATE` out of `ollama.py` into `llm_prompts.py` is a one-import-update change in `claude_llm.py`. No behavior change beyond the simultaneous text rewrite.

### What this does NOT change

- **The editorial gate stays.** `MissionReportEdit` remains the operator's final pass before the customer PDF renders. The prompt fix raises draft quality; the gate stays as the safety net.
- **Provider dispatch stays.** ADR-0014's `llm_provider.py` dispatcher, the managed-instance forcing of Claude, the DB-override for self-hosted — all unchanged.
- **No new alerts.** This defect is below the ADR-0037 notification threshold (no customer impact, no actionable 5-minute window). The regression fixture catches future drift in CI, not in production.

## Rationale

The shared `SYSTEM_PROMPT_TEMPLATE` was written when the product was new and the operator was the only reader of anything. As the customer-facing PDF flow matured (ADR-0011 invoice numbering, ADR-0008 mission-completion gates, ADR-0014 Hub redesign), the audience for every artifact got pinned — *except* the report itself, where the prompt still treats the operator as the conversation partner. ADR-0015 closes that last gap.

The alternative — patching this with post-processing on the LLM output (regex-stripping second-person prose, etc.) — was considered and rejected. Post-processing is brittle, locale-sensitive, and creates a maintenance burden whose value disappears the moment the prompt is correctly written. Get the prompt right; do not paper over a bad prompt.

The alternative of "ship a single combined prompt that produces both a client report and an operator retro" was also considered and rejected. The audiences want different artifacts at different times; conflating them in one LLM call invites exactly the leak this incident documents.

## Verification

- `git grep "SYSTEM_PROMPT_TEMPLATE" backend/app/services/` returns exactly one definition (in `llm_prompts.py` after the move) and two imports (Claude + Ollama).
- The new fixture (`test_llm_report_audience.py`) passes against Ollama in CI.
- A real mission report generated on the personal instance after the prompt change reads as client-facing throughout, including any "Recommendations" section.
- Operator confirms verbatim that the new draft "reads like it is written for the client, not for me."

## Status transitions

- 2026-05-14 — Proposed (this commit).
- Pending — Accepted, when aegis lands the prompt fix and the fixture turns green.
- Pending — Superseded, when (if ever) the operator-retrospective surface ships and an ADR-0016 expands on this one.
