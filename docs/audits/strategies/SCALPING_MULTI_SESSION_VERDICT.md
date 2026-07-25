# Scalping Multi-Session Verdict (auto-generated)

**Status: INTERIM (living document — not final until ≥3 patched sessions)**

**Acceptance state: WAITING_FOR_SESSIONS** — 2/3 patched sessions; 2 with value RANGE_CONFIRMED

Dual-model per patched session (legacy market_timestamp vs shadow value-progression);
300 s threshold unchanged; all gates disabled (`event_gate_enabled` / 
`paper_recording_enabled` / `production_enabled` = false); no trading.

| Session | Uptime% | MaxTsGap | MaxValueGap | TsOnlyFreeze | FullStateFreeze | ConnOutage | EVENT_VALID | Legacy RC | Value RC | Pass |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|:--:|
| 2026-07-21 | 100.0 | 317.0s | 32.7s | 18 | 0 | 0 | 159 | 0 | 159 | True |
| 2026-07-22 | 100.0 | 315.0s | 31.8s | 17 | 0 | 0 | 166 | 0 | 166 | True |

## Answers
1. Valid patched sessions: **2**.
2/3/4. Freeze semantics are per-session above (timestamp-only vs full-state).
5. Material-value gaps are per-session (max column).
6. RANGE_CONFIRMED per model: Legacy vs Value-Progression columns above.
7/8. Sessions with value RANGE_CONFIRMED > 0: **2** (need >=2 of 3).
9. Paper recording eligible: **NO** (still locked — eligibility is not activation).
10. Paper signals recorded: **0** (recording stays locked; eligibility only).
11. Replace production timestamp gate? Only if state is PAPER_RECORDING_ELIGIBLE across sessions AND a human approves; this workflow never replaces it automatically.
12. **No** threshold or strategy changed.
