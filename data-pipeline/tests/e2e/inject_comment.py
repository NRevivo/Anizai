"""
One-shot test utility: inject a single synthetic Polymarket comment Bronze
message into ingest.bronze.polymarket so the E2E runner can validate the
full comment path (Silver social → social_vault + Gold Social Pulse → social_vectors).

The payload is structurally identical to what the real Polymarket producer
would emit — it uses the same build_bronze_message() envelope builder and
the same Bronze schema (Section C.1), so the Silver Job processes it
identically to a real message.

Usage (from data-pipeline/ with venv active):
    python -m tests.e2e.inject_comment
"""

import json
import sys

from utils.kafka_utils import build_bronze_message, make_producer
from config.kafka_topics import BRONZE_POLYMARKET

SYNTHETIC_COMMENT_PAYLOAD = {
    "payload_type": "comment",
    "market_id":    "0x87ae1d3e2aec7cbf6b7f01781f741c82ae9bd08a",
    "question":     "Will the Federal Reserve cut rates before June 2026?",
    "raw_comments": [
        {
            "comment_id": "synthetic_001",
            "author":     "macro_trader_99",
            "text":       "Fed pivot incoming — PCE is cooling fast and T10Y2Y "
                          "inversion is narrowing. I'm pricing this at 72%.",
            "timestamp":  "2026-03-29T00:00:00+00:00",
            "upvotes":    14,
        },
        {
            "comment_id": "synthetic_002",
            "author":     "rates_watcher",
            "text":       "Disagree — CPI stickiness in services is still elevated. "
                          "Powell won't move before the June FOMC.",
            "timestamp":  "2026-03-29T00:01:00+00:00",
            "upvotes":    7,
        },
        {
            "comment_id": "synthetic_003",
            "author":     "vol_arb_desk",
            "text":       "Implied probability tracking roughly 65-70%. "
                          "VIXCLS spike last week suggests uncertainty is elevated.",
            "timestamp":  "2026-03-29T00:02:00+00:00",
            "upvotes":    21,
        },
    ],
    "fetched_at": "2026-03-29T00:00:00+00:00",
}


def main() -> None:
    producer = make_producer()
    try:
        msg = build_bronze_message(
            source_name="polymarket",
            source_endpoint="https://gamma-api.polymarket.com/comments"
                            "?market=0x87ae1d3e2aec7cbf6b7f01781f741c82ae9bd08a",
            raw_payload=SYNTHETIC_COMMENT_PAYLOAD,
        )
        future = producer.send(
            BRONZE_POLYMARKET,
            value=msg,
            key="0x87ae1d3e2aec7cbf6b7f01781f741c82ae9bd08a",
        )
        producer.flush()
        record = future.get(timeout=10)
        print(
            f"[inject_comment] Injected synthetic comment → "
            f"partition={record.partition}  offset={record.offset}"
        )
    except Exception as exc:
        print(f"[inject_comment] ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        producer.close()


if __name__ == "__main__":
    main()
