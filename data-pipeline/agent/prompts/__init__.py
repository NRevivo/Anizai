"""
agent/prompts/ — System prompts and JSON schemas for hub LLM-calling nodes.

Each LLM-calling node owns one module here:
- `query_understanding.py`  (T19.6, used by `agent/nodes/query_understand.py`)
- `evidence_rating.py`      (T20.2, used by `agent/nodes/rate_evidence.py`)

See `README.md` for the full prompt index, output Pydantic models,
and last-reviewed dates.

The package is import-cheap by design — modules contain only constants
(strings, dicts, enums) and pure helpers. No SDK imports, no I/O.
"""
