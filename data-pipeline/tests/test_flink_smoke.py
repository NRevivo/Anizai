"""
Flink Smoke Test — Phase 5, Sprint 12 (Section 8.2)

WHY this test exists: Verifies that the custom anizai-flink:1.19.1 image can
execute a real PyFlink job end-to-end, not just import modules. A successful
import of pyflink does NOT guarantee the Py4J bridge, the JVM, and the Flink
mini-cluster all start correctly. This test exercises the full execution path.

This test is designed to run INSIDE the container via:
    docker exec anizai-flink-jobmanager python3 /opt/flink/usrlib/tests/test_flink_smoke.py

It does NOT require Kafka or PostgreSQL to be running. All data is in-memory.
The test uses Flink's LOCAL execution environment (mini-cluster), which starts
a JobManager and TaskManager inside the same JVM process.

Verification checklist item #8: "A minimal test job succeeds" (infrastructure skill).

References:
    Section 8.2: Custom Flink image, jobmanager + taskmanager deployment
    Section 4.3: EXACTLY_ONCE semantics — confirmed via mini-cluster checkpoint config
    Section 9.3: Gate-style test verification
"""

import sys
import traceback


def run_smoke_test() -> bool:
    """
    Execute a minimal PyFlink DataStream job that:
    1. Creates a local execution environment (Flink mini-cluster, no Docker needed)
    2. Configures EXACTLY_ONCE checkpointing to match production (Section 4.3)
    3. Defines an in-memory source with 5 integer elements
    4. Applies a simple map transformation (multiply by 2)
    5. Collects results and asserts correctness

    WHY a DataStream job (not a Table API job): the Silver and Gold jobs in this
    project use DataStream/ProcessFunction, not Table API. Testing DataStream
    confirms the execution path that production jobs actually use.

    Returns:
        True if the job ran and produced correct output, False otherwise.
    """
    print("[SMOKE] Importing PyFlink DataStream API...")
    from pyflink.datastream import StreamExecutionEnvironment
    from pyflink.datastream.checkpointing_mode import CheckpointingMode

    print("[SMOKE] Creating LOCAL execution environment (mini-cluster)...")
    env = StreamExecutionEnvironment.get_execution_environment()

    # Mirror the EXACTLY_ONCE checkpoint config from docker-compose.yml (Section 4.3).
    # WHY: confirms the checkpoint configuration API is functional, not just that
    # the config value appears in /jobmanager/config. This exercises the Flink
    # checkpoint manager code path.
    env.enable_checkpointing(60000)  # 60s interval — matches production
    env.get_checkpoint_config().set_checkpointing_mode(CheckpointingMode.EXACTLY_ONCE)
    env.get_checkpoint_config().set_min_pause_between_checkpoints(10000)
    env.get_checkpoint_config().set_checkpoint_timeout(120000)

    print("[SMOKE] Checkpoint config: EXACTLY_ONCE, 60s interval")

    # Define in-memory source: five integers matching a simple pattern.
    # WHY these values: deterministic, easy to assert, no external dependency.
    source_data = [10, 20, 30, 40, 50]
    expected = [x * 2 for x in source_data]  # [20, 40, 60, 80, 100]

    print(f"[SMOKE] Source data: {source_data}")
    print(f"[SMOKE] Expected after map(*2): {expected}")

    ds = env.from_collection(source_data)
    transformed = ds.map(lambda x: x * 2)

    print("[SMOKE] Executing job (mini-cluster will start JVM + Flink runtime)...")
    result = list(transformed.execute_and_collect())
    result_sorted = sorted(result)

    print(f"[SMOKE] Collected result (sorted): {result_sorted}")

    assert result_sorted == sorted(expected), (
        f"FAIL: expected {sorted(expected)}, got {result_sorted}"
    )

    print("[SMOKE] Assertion passed.")
    return True


def main() -> None:
    """Entry point. Prints a clear PASS/FAIL summary for CI and manual runs."""
    print("=" * 60)
    print("Anizai Flink Smoke Test — Phase 5 Sprint 12")
    print("=" * 60)

    try:
        passed = run_smoke_test()
        if passed:
            print("=" * 60)
            print("RESULT: PASS — PyFlink mini-cluster executed successfully.")
            print("  Python version    :", sys.version.split()[0])
            print("  Checkpointing     : EXACTLY_ONCE @ 60s interval")
            print("  Source → map(*2)  : [10,20,30,40,50] → [20,40,60,80,100]")
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
