# agent/prompts/ — Prompt Index

System prompts and JSON schemas for the Agentic Hub's LLM-calling nodes.
One file per distinct prompt; each file is import-cheap (constants and
pure helpers only — no SDK imports, no I/O).

Prompts in this directory follow the `agent-prompt-engineering` skill
(reasoning under uncertainty, calibration, permission to admit gaps),
which is distinct from the data-pipeline `prompt-engineering` skill
(deterministic Gold-layer extraction with anchored numeric scales).

| File | Used by | Output schema | Last reviewed |
|---|---|---|---|
| `query_understanding.py` | `agent/nodes/query_understand.py` (T19.5) | inline JSON schema, top-3 candidates with closed enums (intent, domain) | 2026-04-30 |
| `evidence_rating.py` | `agent/nodes/rate_evidence.py` (T20.1) | `EvidenceRatingResponse` strict JSON schema — list of `{evidence_id, relevance_score (0-1), justification}` per batch | 2026-05-04 |

## Conventions

- **Structured output via OpenAI strict JSON schema** (not langchain
  `with_structured_output()`). Sprint 19 standardized on raw OpenAI
  SDK + `response_format={"type": "json_schema", "strict": true}`.
  Both forms are "good" structured output per agent-prompt-engineering
  skill P1; matching the existing pattern keeps node code uniform.
- **Pydantic models for type safety** live in `agent/schemas.py` and
  mirror the JSON schemas above. They are used at construction
  (`rate_evidence` builds dicts that conform to `EvidenceItem`) but
  not as the LLM-side enforcement mechanism.
- **System prompts under 800 words** (P5). Domain context, role,
  task, calibration anchors, permission-to-admit-gaps, 1-2 few-shot
  examples. No raw JSON instruction soup.
- **User messages built by `build_user_message()`** helpers in each
  prompt module. Keeps prompt-construction logic out of node code so
  prompts can be audited and tested independently.

## Adding a new prompt

1. Define the Pydantic output model in `agent/schemas.py` (or inline if
   single-use).
2. Write the system prompt + JSON schema + `build_user_message()` in a
   new file here.
3. Add a row to the table above with file, consumer node, output schema
   summary, and review date.
4. Update `__init__.py` docstring to list the new file.
5. Test against 3+ fixtures (Gate 1) before wiring the consumer node.

See `.claude/skills/agent-prompt-engineering/SKILL.md` for the full
checklist when designing a new hub prompt.
