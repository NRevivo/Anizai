"""
T6b — Flink Kafka Connector Connectivity Test (Section 8.2)

WHY this test exists: Verifies that the flink-sql-connector-kafka-3.3.0-1.19.jar
loaded in the custom image can actually connect to the live Kafka broker and read
messages using PyFlink's KafkaSource. This is distinct from T6 (local mini-cluster):
T6 proved PyFlink's execution runtime works; T6b proves the Kafka connector works.

A successful connector import + local env is NOT sufficient proof — the connector
JAR could be present but incompatible. Only a real read-from-topic round trip confirms
the connector is fully functional (Section 8.2 verification checklist item #8).

Run from INSIDE the jobmanager container:
    docker exec anizai-flink-jobmanager python3 /tmp/test_flink_kafka_connectivity.py

Prerequisites:
    - anizai-kafka container running and healthy (port kafka:29092 reachable)
    - ingest.bronze.polymarket topic exists (created by kafka-init)

Protocol:
    Step 1 — Produce one test message via kafka-python-ng (fast, synchronous).
    Step 2 — Read it back using PyFlink KafkaSource in bounded mode.
             Bounded mode (earliest → latest) terminates after reading all
             currently available messages — no infinite stream.
    Step 3 — Assert >= 1 message was received and print a sample.

References:
    Section 8.2: Kafka connector requirement, internal broker address kafka:29092
    Section 3.1: Topic naming — ingest.bronze.polymarket is a valid Bronze topic
    Section 4.3: EXACTLY_ONCE — Flink job uses local env with checkpoint config
"""

import sys
import json
import traceback

# Internal Docker network address for the Kafka broker (Section 8.2).
# WHY NOT localhost:9092: that is the EXTERNAL listener for host-to-Kafka traffic.
# Inside the Docker network, brokers are reachable at kafka:29092 (INTERNAL listener).
KAFKA_BOOTSTRAP = "kafka:29092"
TEST_TOPIC = "ingest.bronze.polymarket"
CONSUMER_GROUP = "anizai-flink-smoke-t6b"


def step1_produce_message() -> int:
    """
    Produce one JSON test message to the Bronze topic via kafka-python-ng.
    WHY kafka-python-ng and not PyFlink KafkaSink: keeping the producer simple
    (sync, one message) isolates the test — if the Flink read fails, we know
    the issue is in the consumer/connector, not the producer.

    Returns the offset of the produced message.
    """
    from kafka import KafkaProducer

    print(f"[T6b STEP 1] Producing test message to {TEST_TOPIC} at {KAFKA_BOOTSTRAP}...")
    producer = KafkaProducer(
        bootstrap_servers=KAFKA_BOOTSTRAP,
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        request_timeout_ms=10000,
        acks="all",   # wait for broker ACK before returning
    )
    test_payload = {
        "smoke_test": True,
        "source": "t6b_flink_kafka_connectivity",
        "topic": TEST_TOPIC,
    }
    future = producer.send(TEST_TOPIC, value=test_payload)
    record = future.get(timeout=15)
    producer.flush()
    producer.close()
    print(f"[T6b STEP 1] Message written — partition={record.partition}, offset={record.offset}")
    return record.offset


def step2_flink_read() -> list:
    """
    Use PyFlink KafkaSource (flink-sql-connector-kafka-3.3.0-1.19.jar) to read
    all messages currently in the Bronze topic.

    Bounded mode (earliest → latest): the job reads from offset 0 up to the
    current end-of-topic, then terminates. No infinite stream, no timeout needed.

    WHY SimpleStringSchema: the smoke test does not need to deserialize Avro or
    NDJSON — raw string bytes confirm the connector round-trip works.
    """
    from pyflink.datastream import StreamExecutionEnvironment
    from pyflink.datastream.connectors.kafka import KafkaSource, KafkaOffsetsInitializer
    from pyflink.common.serialization import SimpleStringSchema
    from pyflink.common import WatermarkStrategy
    from pyflink.datastream.checkpointing_mode import CheckpointingMode

    print(f"[T6b STEP 2] Creating PyFlink env and KafkaSource for {TEST_TOPIC}...")
    env = StreamExecutionEnvironment.get_execution_environment()
    # Mirror production checkpoint config (Section 4.3)
    env.enable_checkpointing(60000)
    env.get_checkpoint_config().set_checkpointing_mode(CheckpointingMode.EXACTLY_ONCE)

    source = (
        KafkaSource.builder()
        .set_bootstrap_servers(KAFKA_BOOTSTRAP)
        .set_topics(TEST_TOPIC)
        .set_group_id(CONSUMER_GROUP)
        # Start from the very beginning of the topic so the test message is included
        # even if other messages were produced earlier.
        .set_starting_offsets(KafkaOffsetsInitializer.earliest())
        # Bounded stop: read up to the current latest offset, then terminate.
        # WHY .set_bounded: without this, execute_and_collect() would block forever
        # on an unbounded Kafka source.
        .set_bounded(KafkaOffsetsInitializer.latest())
        .set_value_only_deserializer(SimpleStringSchema())
        .build()
    )

    ds = env.from_source(source, WatermarkStrategy.no_watermarks(), "Kafka-Bronze-T6b")

    print("[T6b STEP 2] Executing bounded Flink job — reading topic until current latest offset...")
    messages = list(ds.execute_and_collect())
    print(f"[T6b STEP 2] Collected {len(messages)} message(s) from {TEST_TOPIC}")
    return messages


def main() -> None:
    print("=" * 60)
    print("T6b — Flink Kafka Connector Connectivity Test")
    print(f"  Broker  : {KAFKA_BOOTSTRAP}")
    print(f"  Topic   : {TEST_TOPIC}")
    print("=" * 60)

    try:
        step1_produce_message()
        messages = step2_flink_read()

        assert len(messages) >= 1, (
            f"Expected >= 1 message from {TEST_TOPIC}, got {len(messages)}. "
            f"Check that kafka-init ran and the topic exists."
        )

        # Print the last message (most likely our test message)
        sample = messages[-1][:120] if messages else "(empty)"
        print(f"[T6b] Sample (last message): {sample}")

        print("=" * 60)
        print("RESULT: PASS — Flink KafkaSource read from live Bronze topic.")
        print(f"  Messages read : {len(messages)}")
        print(f"  Broker        : {KAFKA_BOOTSTRAP}")
        print(f"  Topic         : {TEST_TOPIC}")
        print(f"  Connector     : flink-sql-connector-kafka-3.3.0-1.19.jar")
        print("=" * 60)
        sys.exit(0)

    except Exception as exc:
        print("=" * 60)
        print("RESULT: FAIL")
        print(f"  Error: {exc}")
        traceback.print_exc()
        print("=" * 60)
        sys.exit(1)


if __name__ == "__main__":
    main()
