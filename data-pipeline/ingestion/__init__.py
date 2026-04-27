"""
Ingestion Package — Bronze Layer Producers (Section 2).

Each producer is a standalone service responsible for fetching data
from a single external source and emitting raw NDJSON to its
dedicated Bronze Kafka topic. Producers perform ingestion ONLY —
no transformation, no DB writes (Section 3.3 Service Isolation).

Producer Matrix (11 sources): Section 2.1
Bronze Schema: Section C.1
"""
