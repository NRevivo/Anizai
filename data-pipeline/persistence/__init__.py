"""
Persistence Package — Multi-Vault Storage Layer (Section 5).

Unified PostgreSQL engine with extensions manages all Silver and Gold data
across four functional Vaults:
  - Knowledge Vault: Full-text documents (JSONB) — Section 5.1
  - Social Vault: Threaded discussions (JSONB) — Section 5.1
  - Vector Vault: Gold embeddings (pgvector HNSW) — Section 5.2
  - Momentum Vault: Time-series metrics (TimescaleDB) — Section 5.3
  - Mapping Dictionary: Cross-platform ID linkage — Section 5.4

All DB logic lives exclusively in this package (Section 3.3 Service Isolation).
"""
