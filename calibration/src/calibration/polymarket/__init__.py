"""
Polymarket adapter — the only place calibration talks to Polymarket.

Three concerns, deliberately separated so the two that contain judgement can
be tested without a network:

    client.py    — HTTP only. Knows about URLs, timeouts, and retries.
    taxonomy.py  — tag -> category mapping and the block/allow decision.
    discover.py  — pure filtering: which markets qualify, in which cohort.
    resolve.py   — pure parsing: what a CLOB market payload means.

`discover` and `resolve` never perform I/O. They take payloads and return
decisions, which is what makes the fixture-driven tests in
`tests/test_calibration/` meaningful rather than mock theatre.

The Gamma and CLOB base URLs are copied from
`ingestion/polymarket_producer.py`, not imported: that module pulls asyncio,
websockets, and a Kafka producer at import time, none of which calibration
needs and all of which would couple the two systems (plan §6).
"""
