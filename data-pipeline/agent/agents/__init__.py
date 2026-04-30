"""
agent/agents/ — vault-retrieval sub-components called by the `vault_query`
LangGraph node.

Each module exposes a single `run(...)` function that takes plain inputs
(query embedding, canonical_event_id, etc.) and returns a structured
evidence dict matching `data-pipeline/docs/agentic_hub_spec.md` §8.4.1-§8.4.3.

The `vault_query` node (T19.8) dispatches these in parallel via
`asyncio.to_thread` — keeping each agent sync matches the underlying
psycopg2 persistence layer and isolates async concerns to the node.

Sprint 19 contains:
    - researcher.py    (§8.4.1) — knowledge_vectors + knowledge_vault
    - pulse_analyst.py (§8.4.2) — social_vectors  + social_vault       (T19.3)
    - market_bridge.py (§8.4.3) — momentum_vault  + mapping_dict       (T19.4)

Per Sprint 19 D2: agentEvents emission and budget tracking are deferred
to Sprint 25 / Sprint 22+ respectively. Agents are pure read-and-pack.
"""
