# Phase 7D — Cloud verification close pack (verdict summary)

> Window RUN_ID: `phase7d-verify-20260727` · `:t_start (attempt 2) = 2026-07-27T12:38:53Z`
> Files: `baseline_pre_window.csv` (C0), `gate_skips.log` (C1), `gate_effectiveness.csv` (C2/C4),
> `wasted_enrichment.csv` (C3), `hn_rejects.csv` (C5/C6), `cost_health.csv` (C8), `health.txt` (C9),
> `image_freeze_diff.txt`, `truncation_offsets.json`, `deployment_log.md` (full narrative).

## LEAD LIMITATION — the social path is the weakest part of this result
The social (HackerNews) path **never saw live multi-pulse traffic**. It rests on an accumulated queue:
**52 distinct stories, only 2 high-signal, 10 T4 skips.** The T4 dedup gate **fired** (first execution of that
code anywhere) but on **n=2** — this is **fired, not verified.** Treat HN gate effectiveness as demonstrated-in-
principle, not measured. The reject-capture half (KG-A-12) is stronger: 288 HN rejects / 50 distinct captured with
0 NULL keys.

## Window design changed — live-traffic → queue-processing (deliberate)
P6 sized 90 minutes to collect live HN pulses. After the OOM incident, the window ran instead on the **drained
accumulated queue** (producers off, DAGs paused). Sitting to 14:08Z would collect zero further data, so the window
was closed at drain. This is a design change, not a shortcut: the accumulated queue already held real newsapi/HN/
arxiv duplicates, which is what C2/C3/C4 need.

## Verdict table
| Check | Result | Verdict |
|---|---|---|
| C0 baseline | captured pre-window (offsets, vault debt kv 27,459 vs vault 2,759 = 9.95x, HN enrich 12.4x) | ✅ |
| C1 gate fires | **46 newsapi + 1,748 arxiv + 10 HN** `[gold/dedup]` skips (unsampled); 6 startup lines correct | ✅ |
| C2 newsapi | 62 enrich = 62 archived (1:1); pre-gate 108 → 62, 1.74→1.00 | ✅ |
| C3 wasted enrich | 0 | ✅ |
| C4 arxiv | 102 enrich = 102 archived (1:1); 1,850 would-be → 102 = 94.5% cut (two runs, ~9x/run) | ✅ **headline** |
| C5 HN rejects (KG-A-12) | 288 rows / 50 distinct / 0 NULL keys — source that captured nothing before | ✅ |
| C6 instance key | 0 NULL `canonical_event_id`, all sources | ✅ |
| C7 KG-A-8 (vectors) | in-window newsapi 62=62, arxiv 102=102 vectors=vault (1:1); whole-table debt = C0 (historical) | ✅ |
| C8 cost health | 1717 calls, $0.0761, **0 empty trace_id** (KG-A-13 held) | ✅ |
| C9 no regression | DLQ delta 0 (7142); jobs RUNNING; TM restarts 0 | ✅ |

## Gaps stated plainly (do not assume coverage)
- **telegram (the §9 control source) was NOT exercised** — producer at 0, Bronze truncated, no data. Its role
  (gate must not over-suppress a duplicate-free source) is served instead by **enrich = distinct on both news
  sources: 62/62 and 102/102 — every first occurrence was enriched.**
- **Social path — fired, not verified** (n=2 high-signal stories). See LEAD LIMITATION.
- **§6 named ordering assertion — LIVE confirmation:** 288 HN rejects over 50 distinct low-signal stories (5.8x)
  proves the T6 reject-capture runs BEFORE the T4 dedup gate in production, not just in the AST test.

## Infra finding (not a 7D code result)
The `-7d` code is verified. The cloud measurement was blocked twice by a TaskManager OOM traced to
`LOG_INFO_SAMPLE_RATE=1.0` (the differentiating variable across three attempts at constant memory; flat JVM heap
places growth Python-side). **Mechanism NOT established** (if INFO is unsampled with the var absent, 1.0 cannot
multiply volume). Recorded as an unresolved T12 question + a documentation defect (trap 3 / §11 claim about the
Flink jobs looks false). The day-run (2560Mi, all 9 sources, 24h) was Prometheus-clean, so this is NOT KG-A-9.

## NEW — social-path signal-flipping lockout (Known Gap for T12; follows from D1a, NOT a defect to fix)
On the social path, archival happens **before** the `is_high_signal` check. So a low-signal HN story on its
FIRST delivery is archived (`social_vault`) + reject-captured, then returns. On any LATER delivery, if it is now
high-signal, `exists_by_content_hash` is already true → the T4 gate skips it → it is **never enriched**. Under the
old comment-inclusive hash this could not happen (each delivery carried a fresh hash). So D1a in practice may mean
**"a story is enriched only if it was high-signal on its FIRST delivery"** — a **coverage** reduction, not just a
duplication reduction; it can change WHICH stories get enriched, so C2's hackernews row must be read that way.
**This slice: NOT demonstrated (disjoint).** The 2 enriched stories (49057248, 49057574) have zero overlap with
the 288 rejects — both high-signal on first delivery. Flipping not observed here; effect not shown. **Record as a
Known Gap for T12** (it follows from D1a as approved — not a 7D fix).

## NEW — first D2 counter-example: an HN reject cosine ABOVE the promote threshold (7B.5 hand-off)
HN max `rescue_cosine` = **0.3523 > 0.35** (the promote threshold). D2's premise was that the day-run max (~0.345)
sat below it, so "almost nothing is given up." This is the **first counter-example**: one real HN story (1 of 288)
that would have been rescued had the promote branch been wired. n=1 → not a reversal, but it is exactly D2's
revisit condition — hand to 7B.5. And per **D4**, HN cosines are NOT comparable to the news-calibrated 0.35 (the
sniper reference vector is news-built), which is precisely why 7B.5 must set a SOCIAL threshold on social data.

## 7B.5 hand-off finding
Per run, high-signal arxiv papers arrive **~9× each** (18.1× / 2 runs) vs low-signal **~1.05×** (2.09× / 2 runs) —
a **~9:1** ratio. Duplication correlates strongly with the sniper passing; cross-listing (interdisciplinary papers
appearing under many category queries) may itself be a relevance signal. 7B.5's territory.
