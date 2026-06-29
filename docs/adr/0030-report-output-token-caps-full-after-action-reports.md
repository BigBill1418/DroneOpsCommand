# ADR-0030 — Report output-token caps: full after-action reports must complete

- **Status:** Accepted
- **Date:** 2026-06-29
- **Supersedes/Amends:** none (operational tuning of the report-generation path
  introduced alongside ADR-0029)
- **Version:** v2.76.2

## Context

Mission after-action reports were truncating mid-sentence in the client portal
preview. Every report died at ~2,895 characters. The stored savannah report
(mission `e5f3aedf-c3a2-46d2-9438-33a3cb8f3f8f`) had `final_content` ==
`llm_generated_content` == exactly **2,895 chars**, ending mid-word
("…wide-establ").

Root cause was a too-small **output-token cap** on the report-generation LLM
calls, present on both provider paths:

| Path | File | Symptom |
|------|------|---------|
| Claude (live / managed) | `backend/app/services/claude_llm.py` | `max_tokens=1024` |
| Ollama (self-hosted) | `backend/app/services/ollama.py` | `num_predict=1024` output cap **and** `num_ctx=2048` window |

The live BOS-HQ instance runs the **Claude** path (`system_settings.llm_provider
= 'claude'`, anthropic key set). The worker log proves the cap was the cause:

```
doc.claude_llm  Claude report generated: 2895 chars, 3426 input tokens, 1024 output tokens
```

1024 output tokens ≈ 2,895 chars — the generation was cut off exactly at the cap,
not because the model finished.

The Ollama path had a second, compounding defect: `num_ctx=2048` is smaller than
the **input** alone (the savannah prompt is ~3,426 input tokens). For Ollama,
`num_ctx` must be ≥ input_tokens + num_predict or the output is squeezed/
truncated even before the output cap is reached. The system prompt grew with the
ADR-0029 altitude prohibition, making 2048 even more inadequate.

No other truncation exists in the report path (audited): the `final_content` /
`llm_generated_content` DB columns are unbounded `Text`; there is no `[:N]`
slice, `.truncate()`, length limit, or frontend line-clamp on report content
(`MissionReportEdit.tsx`, the client-portal PDF path, and `routers/reports.py`
all carry the full string). The 1024-token cap was the sole clip.

## Decision

Raise the output caps generously so a full after-action report always completes,
and raise the Ollama context window so it holds input + output:

| Knob | Before | After |
|------|--------|-------|
| Claude `max_tokens` | 1024 | **4096** |
| Ollama `num_predict` (output) | 1024 | **4096** |
| Ollama `num_ctx` (window) | 2048 | **8192** |

Rationale:

- A full report runs ~1.5–2.5k tokens. **4096** gives clear headroom (~1.6–2.7×)
  without being wasteful.
- Ollama `num_ctx = 8192` holds the savannah-class input (~3.4k tokens) plus the
  full 4096-token output with margin. The deployed self-hosted model
  (`llama3.1:8b-instruct-q4_K_M`) has a native context length of **131,072**, so
  8192 is trivially supported; the added KV cache is ~1 GB and the BOS-HQ host
  has >20 GB free — verified safe.
- Claude's context is 200k, so the input side is never the constraint on that
  path; only `max_tokens` mattered.

A regression test
(`backend/tests/test_report_output_token_caps_adr0030.py`) asserts both output
caps are ≥ 4096 and that Ollama `num_ctx` ≥ 8192 and exceeds `num_predict`, so
this cannot silently regress.

## Consequences

- Full reports generate end-to-end on both provider paths.
- The ADR-0029 altitude prohibition is unaffected — the longer output gives the
  model more room, but the prohibition system-prompt clauses and the runtime
  deterministic guard (`services/report_audience.py`,
  `_apply_audience_findings`) remain intact. The regenerated full savannah
  report was re-scanned: zero altitude-limit / 400 ft / Part-107-exceedance
  matches.
- **Graceful degradation note:** a normal mission report will not approach 4096
  output tokens. For the *self-hosted Ollama* path only, a pathologically large
  mission whose input exceeds ~4k tokens could compress the available output
  window (input + output > 8192); the production deliverable path is Claude
  (200k context, unaffected). If self-hosted very-large-mission reports are ever
  observed truncating, raise Ollama `num_ctx` further (the model supports
  131,072 and the host has ample memory).

## Failover & Resilience Guard

- No port bindings, connection strings, replication, or compose topology
  touched. Runtime-only LLM option changes baked into the backend/worker image.
- Survives container recreation (code change, not runtime mutation).
- No impact on blue-green swap, the failover engine, or any customer-facing
  service during a site failover.
