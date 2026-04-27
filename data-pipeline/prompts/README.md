# Prompts — AI Enrichment System Prompts

All GPT-4o system prompts for the Gold layer live here (Section 4.2A).
`gold_job.py` imports prompt constants from this package — prompts are never defined inline.

## Prompt Catalogue

| File | Exported Constant | Sources | Fields Returned | Last Reviewed |
|------|-------------------|---------|-----------------|---------------|
| `cognitive_metadata.py` | `COGNITIVE_METADATA_SYSTEM_PROMPT` | NewsAPI, ArXiv, Telegram | `executive_summary`, `key_findings`, `impact_level`, `urgency_level`, `reliability_score`, `sentiment_score`, `extracted_entities`, `topic_classification`, `fact_check_flag`, `geospatial_focus` | Sprint 11 (2026-04-09) |
| `consensus_summary.py` | `CONSENSUS_SYSTEM_PROMPT` | Polymarket, HackerNews | `executive_summary`, `key_findings`, `impact_level`, `urgency_level`, `reliability_score`, `sentiment_score`, `extracted_entities`, `topic_classification`, `fact_check_flag`, `consensus_rating`, `uncertainty_index` | Sprint 11 (2026-04-09) |
| `translation.py` | `TRANSLATION_SYSTEM_PROMPT` | Telegram (Silver layer — non-English channels only) | Plain text — translated English string (not JSON) | Sprint 13 (2026-04-10) |

## Anchored Scale Definitions (shared across both prompts)

All numeric fields use the same anchored scales for consistency:

### impact_level (integer 1–5)
| Value | Anchor |
|-------|--------|
| 1 | Negligible — Local/isolated event; no measurable effect |
| 2 | Minor — Sector-limited; fades within days; market move <1% |
| 3 | Moderate — Multi-sector; mainstream coverage; lasts weeks |
| 4 | Significant — Broad market or macro policy impact; multiple asset classes or major index; OR significant military escalation (cross-border strikes, naval blockade); OR major tech disruption (critical zero-day, major cloud outage, breakthrough AI regulation) |
| 5 | Critical — Systemic/global; central bank emergency action, declared war, pandemic, or major financial institution collapse |

### urgency_level (integer 1–5)
| Value | Anchor |
|-------|--------|
| 1 | Evergreen — No time pressure; relevant for months/years |
| 2 | Watch — Developing; days to weeks |
| 3 | Timely — Breaking; hours to a day |
| 4 | Urgent — Active fast-moving; minutes to hours |
| 5 | Flash — Real-time crisis; minutes |

### reliability_score (float 0.0–1.0)
| Range | Anchor |
|-------|--------|
| 0.0–0.2 | Unreliable — Unverified rumor; no corroboration |
| 0.2–0.4 | Low — Speculative/opinion; no primary attribution |
| 0.4–0.6 | Moderate — Established outlet but secondary source |
| 0.6–0.8 | High — Reuters/AP/FT/WSJ/official gov release |
| 0.8–1.0 | Authoritative — Central bank statement, SEC filing, peer-reviewed paper |

### sentiment_score (float −1.0 to +1.0)
| Range | Anchor |
|-------|--------|
| −1.0 to −0.6 | Strongly negative — Crisis language; mass sell-off signals |
| −0.6 to −0.2 | Mildly negative — Caution; bearish outlook |
| −0.2 to +0.2 | Neutral — Factual; no directional bias |
| +0.2 to +0.6 | Mildly positive — Optimism; growth narrative |
| +0.6 to +1.0 | Strongly positive — Bullish conviction; breakthrough framing |

## Rules
- Never define prompts inline in `gold_job.py` or `silver_job.py`
- One file per distinct prompt type (not per source)
- If sources share a schema, they share a file
- Update "Last Reviewed" date in the file docstring after any revision
- Run all 6 Gold gate tests after any prompt change to confirm no regressions
- **Exception — plain-text prompts:** `translation.py` returns plain text, not JSON.
  This is intentional: translation has a single string output with no metadata schema.
  All other enrichment prompts must still return JSON.
