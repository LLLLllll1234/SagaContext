# Real Judge v3 DeepSeek Single-Request Diagnostics

**Date:** 2026-09-06 (Asia/Shanghai)
**Dataset:** `real-judge-v3`
**Case:** `v3-smoke-confirm-convention`
**Timeout:** 60 seconds

These are independent one-observation probes. They are not merged with the
prior `real-judge-v2` runs and do not constitute a full acceptance run.

| Model | Run ID | Latency | Status | HTTP status | Validation/detail | Response digest | Auxiliary trace |
|---|---|---:|---|---:|---|---|---|
| `deepseek-v4-pro` | `20260906T100914Z-diag-v3-deepseek-pro-52fedd81` | 12646 ms | `ok` | 200 | `delta-v3` valid; conversion valid | `8abb0faff8e0cbf27022b5c5a2e6706bcf203168f7bf2c42ade2a47e99637138` | `strong_signal=null`, `confidence_hint=null` |
| `deepseek-v4-flash` | `20260906T095910Z-diag-v3-deepseek-flash-bc580943` | 5508 ms | `judge_schema_error` | 200 | `invalid delta schema at 0.layer` | `sha256:30084ce3c886f96a04df0d2a6766fca763bad69413d76acf4871e64f91494ff8` | unavailable because validation failed |

## Interpretation

Pro passed the request, JSON, wire-schema, conversion, and single-case semantic
checks. Flash reached the same HTTP/JSON path but emitted a value outside the
`layer` enum, so no semantic score is assigned and no 42-observation run is
authorized for Flash.

Successful HTTP responses that fail parsing or schema validation now retain the
non-sensitive response status (`200`) in `JudgeError` and replay artifacts;
validation detail remains limited to the safe Pydantic location. Response bodies,
authorization data, and full endpoints are not stored.

Normal-session automatic writes, resident workers, and automatic injection remain
disabled.
