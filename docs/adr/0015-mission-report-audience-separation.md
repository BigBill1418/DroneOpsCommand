# ADR-0015: Mission reports are client-facing artifacts; operator-facing coaching is explicitly out of scope

**Status:** Accepted (2026-05-14) — operator-confirmed at close-out.
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

DroneOpsCommand produces **exactly one** LLM-generated report artifact, and it is **client-facing**. Operator-facing coaching, retrospectives, debriefs, or self-review are **explicitly out of scope** for this system. The following decisions enforce that contract.

1. **The mission report `final_content` is a client-facing artifact, full stop.** Its audience is the paying customer (the `Customer` row joined to the `Mission`). The LLM prompt names the customer (by name if available, otherwise by role: "the customer who commissioned this mission"). The operator is the *author of the source notes*, not a reader of the report.

2. **There is no operator-facing debrief surface.** No second LLM call, no second `Report` field, no parallel "operator retrospective." This is not a deferred feature — it is a non-goal. Operator self-review, if the operator wants it, happens outside this system (separate notes, separate tooling, the operator's own process). See "Rejected alternative" below.

3. **The prompt enforces the separation (prompt-as-contract).** The system prompt explicitly:
   - Names the audience (the customer).
   - Names the operator as the upstream author of the input narrative, not the reader.
   - Forbids second-person address to the operator.
   - Forbids flight-technique advice, operator coaching, gear suggestions, or any prose whose target reader is the operator.
   - Reframes Section 5 "Recommendations" to mean *customer follow-up actions* (e.g., "schedule a re-flight after the next rainfall," "review the marked anomalies on page 3"). If the LLM has nothing to recommend *to the customer*, the section is omitted entirely — not filled with operator coaching.

4. **A regression fixture pins the contract (regression-fixture invariant).** A test suite (`backend/tests/services/test_report_audience_guard.py`, landed at commit `22469ed`) locks the structural guarantees of the system prompt (Layer 1: audience pin, operator-address ban, Section-5 reframe, operator-notes framing) and exercises a deterministic regex-based audience-leak detector against representative bad phrasings (Layer 2). Any future change to the prompt is a code review against a known invariant. Hermetic — no network, no LLM, no DB.

5. **Runtime audience-leak detector as a soft-block gate on every generated report.** The detector module (`backend/app/services/report_audience.py`, landed at commit `22469ed`) is wired as a post-generation gate on every LLM-produced report draft. On leak detection the draft is flagged (not silently passed through) and surfaced in the `MissionReportEdit` editorial UI with the offending phrasings highlighted. Soft-block means the operator can still override and ship, but the leak is never invisible. **Shipped (local) at commit `4953edf` (2026-05-14).** The wire-in lives at the persistence site (`backend/app/tasks/celery_tasks.py:150-189`, helper `_apply_audience_findings`) rather than per-provider call paths, so it covers both Claude and Ollama without duplication. The helper never raises (detector failure logs and leaves defaults, so generation never 500s on a regex regression). Per operator directive, the gate performs **detection + surfacing only — no regen loop**; a doc-string-lock test (`test_no_regen_loop_contract_is_documented` in `backend/tests/services/test_audience_leak_persistence.py`) trips CI if a future edit drifts toward a retry-until-clean pattern. Deploy is gated on the 24h soak per decision #6 below.

6. **Verification gate before any push to managed-hosting tenants.** The prompt fix lands on personal instance first, soaks 24h with at least one real mission report generated and reviewed, then propagates to managed tenants via the existing image rollout. No same-day fan-out, per the operator's preference for thoroughness over speed on planned (non-incident) work.

## Rejected alternative: operator-facing debrief surface

An earlier draft of this ADR (Proposed, 2026-05-14 AM) framed the audience leak as "client surface today, operator debrief tomorrow" — i.e., the operator-coaching content that leaked into the client report had a legitimate future home on a separate field, and the work was to *separate* the two surfaces, not to delete one.

**That framing was wrong and is rejected at close-out.** Operator quote:

> "drop it — i don't need a operator debrief — never asked for that."

The operator never requested an AI-generated operator-facing debrief. The "future surface" rationale was Terry-supplied scaffolding, not a product requirement. Building it would have:

- **Expanded scope without business need.** Net-new schema column, net-new prompt, net-new frontend facet, net-new managed-tenant rollout surface — all to satisfy a feature the operator did not ask for.
- **Diluted the audience-separation contract.** Having two LLM surfaces with two prompts that must each stay in their lane is a strictly weaker guarantee than having one surface whose contract is "this is for the customer, period."
- **Invited future leak vectors.** Two prompts that share a codebase, two frontend rendering paths, two test fixtures — each pair is a place where a copy-paste mistake or a future refactor can let operator-facing prose escape into a customer-facing rendering pipeline.

The decision at close-out is the stronger one: **this system produces client-facing reports only.** If operator self-review tooling is ever wanted, it is a different product decision evaluated on its own merits, not a follow-up of this incident.

## Consequences

### What this delivers

- **The mission report is what the operator says it is** — a client-facing account of the mission as flown, written for the customer who paid for the flight. No more audience leak.
- **The prompt becomes the contract.** Co-located, version-controlled, with a fixture pinning the audience guarantee. Any future change to the prompt is a code review against a known invariant.
- **Provider-swap is now safe.** Today the same template is shared across Claude and Ollama. Tomorrow if a customer's tenant runs a third provider, the audience separation comes with it because it is in the prompt, not in any provider adapter.
- **A runtime safety net.** The soft-block gate means future drift (whether from a model upgrade, a prompt edit, or a fine-tune divergence on a managed tenant) cannot silently re-introduce the leak — the editorial UI will surface it.
- **Smaller, tighter product surface.** One report artifact, one audience, one prompt to maintain.

### What this costs

- **No operator-retrospective surface — and that is the point.** If the operator later decides he does want one, this ADR is superseded, not violated. The cost is honesty about scope rather than carrying a phantom roadmap item.
- **One additional CI test suite** (the 17-test regression fixture). Adds ~1.8s to backend suite. Acceptable.
- **One additional runtime check** on every report generation. The detector is regex-based and runs against the generated string in memory; cost is negligible.

### What this does NOT change

- **The editorial gate stays.** `MissionReportEdit` remains the operator's final pass before the customer PDF renders. The prompt fix raises draft quality; the soft-block gate raises visibility into drift; the editorial gate stays as the human-in-the-loop safety net.
- **Provider dispatch stays.** ADR-0014's `llm_provider.py` dispatcher, the managed-instance forcing of Claude, the DB-override for self-hosted — all unchanged.
- **No new alerts.** This defect class is below the ADR-0037 notification threshold (no customer impact, no actionable 5-minute window). The regression fixture catches future drift in CI; the soft-block gate catches it at runtime in the editorial UI. Neither paths to ntfy.

## Rationale

The shared `SYSTEM_PROMPT_TEMPLATE` was written when the product was new and the operator was the only reader of anything. As the customer-facing PDF flow matured (ADR-0011 invoice numbering, ADR-0008 mission-completion gates, ADR-0014 Hub redesign), the audience for every artifact got pinned — *except* the report itself, where the prompt still treats the operator as the conversation partner. ADR-0015 closes that last gap by enshrining a single audience for the single artifact.

The alternative — patching with post-processing on the LLM output (regex-stripping second-person prose) — was considered and rejected as the *sole* fix because it is brittle, locale-sensitive, and a maintenance burden whose value disappears the moment the prompt is correctly written. However, regex-based detection has been adopted as a *soft-block gate*, not a fix: it is a tripwire on top of a correct prompt, not a substitute for one.

The alternative of "ship a single combined prompt that produces both a client report and an operator retro" was considered and rejected. The audiences want different artifacts at different times; conflating them in one LLM call invites exactly the leak this incident documents.

The alternative of "build the operator-facing surface alongside the client-facing fix" was considered and rejected at operator confirmation (see "Rejected alternative" above).

## Verification

- The new fixture (`tests/services/test_report_audience_guard.py`) passes against the prompt in CI (17/17 passing on Python 3.12.3, commit `22469ed`).
- A real mission report generated on the personal instance after the prompt change reads as client-facing throughout, including any "Recommendations" section.
- Operator confirms verbatim that the new draft "reads like it is written for the client, not for me."
- Soft-block gate (shipped at commit `4953edf`) flags any future drift in the editorial UI before the operator ships the report — verified by the 10-test `test_audience_leak_persistence.py` suite plus the unchanged 17-test `test_report_audience_guard.py` suite.

## Status transitions

- 2026-05-14 (AM) — Proposed.
- 2026-05-14 (PM) — Accepted at operator close-out: scope tightened (operator-debrief surface explicitly rejected); soft-block runtime gate added as decision #5.
- Pending — Superseded, if and only if a future operator decision reverses the rejection of the operator-facing surface. Until then, this ADR is load-bearing.
