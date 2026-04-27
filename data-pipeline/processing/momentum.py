"""
Momentum Block — Deterministic Delta Calculation (Section 4.2B).

Flink maintains fault-tolerant keyed state for every numerical metric.
Calculates change_24h, change_7d, change_30d as new data arrives,
allowing the RAG agent to answer "What is the trend?" without
performing math at runtime.

References:
    - Section 4.2B: Structural Enrichment — Keyed State & Momentum Block
    - Section C.2: momentum_block in Silver Structured Schema
    - Section 5.3: Momentum Vault (TimescaleDB)
"""
