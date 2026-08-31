# Scalping Multi-Session Verdict (auto-generated)

**Status: FINAL**

**Acceptance state: PAPER_RECORDING_ELIGIBLE** — 24 sessions, 24 qualifying (>=2 of 3)

Dual-model per patched session (legacy market_timestamp vs shadow value-progression);
300 s threshold unchanged; all gates disabled (`event_gate_enabled` / 
`paper_recording_enabled` / `production_enabled` = false); no trading.

| Session | Uptime% | MaxTsGap | MaxValueGap | TsOnlyFreeze | FullStateFreeze | ConnOutage | EVENT_VALID | Legacy RC | Value RC | Pass |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|:--:|
| 2026-07-21 | 100.0 | 317.0s | 32.7s | 18 | 0 | 0 | 159 | 0 | 159 | True |
| 2026-07-22 | 100.0 | 315.0s | 31.8s | 17 | 0 | 0 | 166 | 0 | 166 | True |
| 2026-07-26 | 100.0 | 493.0s | 30.7s | 16 | 0 | 0 | 181 | 0 | 181 | True |
| 2026-07-27 | 100.0 | 622.0s | 35.0s | 17 | 0 | 0 | 180 | 0 | 180 | True |
| 2026-07-28 | 100.0 | 458.0s | 31.7s | 15 | 0 | 0 | 176 | 0 | 176 | True |
| 2026-07-29 | 100.0 | 441.0s | 33.6s | 22 | 0 | 0 | 173 | 0 | 173 | True |
| 2026-07-30 | 99.6 | 460.0s | 32.4s | 22 | 0 | 0 | 171 | 0 | 171 | True |
| 2026-08-02 | 100.0 | 0.0s | 100.3s | 0 | 0 | 0 | 146 | 146 | 146 | True |
| 2026-08-03 | 100.0 | 0.0s | 8.3s | 0 | 0 | 0 | 133 | 133 | 133 | True |
| 2026-08-04 | 100.0 | 0.0s | 5.7s | 0 | 0 | 0 | 116 | 116 | 116 | True |
| 2026-08-05 | 100.0 | 0.0s | 5.0s | 0 | 0 | 0 | 148 | 148 | 148 | True |
| 2026-08-06 | 100.0 | 0.0s | 4.3s | 0 | 0 | 0 | 163 | 163 | 163 | True |
| 2026-08-09 | 100.0 | 0.0s | 4.1s | 0 | 0 | 0 | 145 | 145 | 145 | True |
| 2026-08-10 | 100.0 | 0.0s | 4.3s | 0 | 0 | 0 | 142 | 142 | 142 | True |
| 2026-08-11 | 100.0 | 0.0s | 5.6s | 0 | 0 | 0 | 146 | 146 | 146 | True |
| 2026-08-12 | 100.0 | 0.0s | 1.5s | 0 | 0 | 0 | 121 | 121 | 121 | True |
| 2026-08-13 | 100.0 | 0.0s | 4.9s | 0 | 0 | 0 | 146 | 146 | 146 | True |
| 2026-08-18 | 100.0 | 0.0s | 116.3s | 0 | 0 | 0 | 146 | 146 | 146 | True |
| 2026-08-23 | 100.0 | 0.0s | 3.0s | 0 | 0 | 0 | 142 | 142 | 142 | True |
| 2026-08-24 | 100.0 | 0.0s | 6.1s | 0 | 0 | 0 | 135 | 135 | 135 | True |
| 2026-08-25 | 100.0 | 0.0s | 8.8s | 0 | 0 | 0 | 148 | 148 | 148 | True |
| 2026-08-26 | 100.0 | 0.0s | 4.6s | 0 | 0 | 0 | 134 | 134 | 134 | True |
| 2026-08-30 | 100.0 | 0.0s | 4.6s | 0 | 0 | 0 | 143 | 143 | 143 | True |
| 2026-08-31 | 100.0 | 0.0s | 17.5s | 0 | 0 | 0 | 122 | 122 | 122 | True |

## Answers
1. Valid patched sessions: **24**.
2/3/4. Freeze semantics are per-session above (timestamp-only vs full-state).
5. Material-value gaps are per-session (max column).
6. RANGE_CONFIRMED per model: Legacy vs Value-Progression columns above.
7/8. Sessions with value RANGE_CONFIRMED > 0: **24** (need >=2 of 3).
9. Paper recording eligible: **YES** (still locked — eligibility is not activation).
10. Paper signals recorded: **0** (recording stays locked; eligibility only).
11. Replace production timestamp gate? Only if state is PAPER_RECORDING_ELIGIBLE across sessions AND a human approves; this workflow never replaces it automatically.
12. **No** threshold or strategy changed.
