"""
agent/tools/ — thin wrappers around persistence/* for Sprint 19 retrieval agents
(Researcher, Pulse Analyst, Market Bridge).

Service-isolation invariant (CLAUDE.md §3.3): this directory is the only place
in the hub where `persistence/*` is imported. Agents (`agent/agents/`) and
nodes (`agent/nodes/`) call these wrappers — they never import persistence
modules directly.

Module map:
    knowledge_tools — knowledge_vectors + knowledge_vault (Researcher, §8.4.1)
    social_tools    — social_vectors + social_vault (Pulse Analyst, §8.4.2)
    market_tools    — momentum_vault (Market Bridge, §8.4.3)
    mapping_tools   — mapping_dict (Market Bridge, §8.4.3 step 2)

Drift normalization landed here (audit §4):
    D-1: similarity_score → similarity (social_tools)
    D-2: spec call values passed explicitly, not inherited from persistence defaults
    D-3: parameter shape difference between knowledge_vectors / social_vectors
    D-4: source_name vs source_platform — wrappers expose `source_platforms`
    D-6: metadata_extension JSONB stays as-is on the row; agents extract per source

Spec references:
    - data-pipeline/docs/agentic_hub_spec.md §8.4 (Vault Retrieval Agents)
    - data-pipeline/docs/sprint19_persistence_audit.md
"""
