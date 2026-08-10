"""
Calibration CLI — the operator surface for Phase 10A.

    python -m calibration.cli init-db
    python -m calibration.cli discover [--dry-run] [--today YYYY-MM-DD]
    python -m calibration.cli seed
    python -m calibration.cli resolve [--days-ahead N]
    python -m calibration.cli list-questions [--status ...] [--cohort ...]
    python -m calibration.cli add-manual --slug ... --category ... --cohort ...
    python -m calibration.cli status
    python -m calibration.cli check-config

Deviation from the plan (§6 T10A.12): the plan specified Click. This uses
argparse from the standard library. Click is not currently a dependency of
this repository, and a five-command operator tool does not justify adding one
— particularly for a package whose defining property is that it drags nothing
new into the project. If the command surface grows past what argparse
subparsers handle comfortably, revisit.

Every command that writes runs `config.validate()` first, which is what stops
a mistyped CALIBRATION_DATABASE_URL from reaching the pipeline's vaults.

`--dry-run` on `discover` is the safe default posture for the first run
against live Polymarket data: it performs the fetch and the full filter, and
prints exactly what it would insert, without touching the database.

References:
    - calibration_plan.md §6 T10A.12
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime
from typing import Optional

from calibration import __version__, config

logger = logging.getLogger("calibration.cli")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )


def _print_block(title: str, lines: list[str]) -> None:
    print(f"\n=== {title} ===")
    for line in lines:
        print(f"  {line}")
    print()


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


# ==========================================================
# Commands
# ==========================================================

def cmd_check_config(_args: argparse.Namespace) -> int:
    """
    Print the effective configuration and validate it.

    Deliberately the one command that runs without a database: it is what an
    operator reaches for when something is misconfigured, and needing a
    working DB to diagnose a broken DB URL would be useless.
    """
    db_name = config.parse_database_name(config.CALIBRATION_DATABASE_URL)
    _print_block(
        "Effective configuration",
        [
            f"version                  : {__version__}",
            f"database                 : {db_name}",
            f"CALIBRATION_ENABLED      : {config.CALIBRATION_ENABLED}",
            f"gamma api                : {config.POLYMARKET_GAMMA_API}",
            f"clob api                 : {config.POLYMARKET_CLOB_API}",
            f"targets (7d/14d/30-45d)  : {config.CALIBRATION_TARGET_COUNT_7D}/"
            f"{config.CALIBRATION_TARGET_COUNT_14D}/"
            f"{config.CALIBRATION_TARGET_COUNT_30_45D}",
            f"liquidity floors         : {config.CALIBRATION_LIQUIDITY_MIN_7_14D_USD} "
            f"(7d/14d) / {config.CALIBRATION_LIQUIDITY_MIN_30_45D_USD} (30-45d)",
            f"max open questions       : {config.CALIBRATION_MAX_OPEN_QUESTIONS}",
            f"max forecasts per run    : {config.CALIBRATION_MAX_FORECASTS_PER_RUN} (Phase 10B)",
            f"dispatch concurrency     : {config.CALIBRATION_DISPATCH_CONCURRENCY} (Phase 10B)",
        ],
    )
    try:
        config.validate()
    except ValueError as exc:
        print(f"INVALID: {exc}\n")
        return 1
    print("Configuration is valid.\n")
    return 0


def cmd_init_db(_args: argparse.Namespace) -> int:
    """Apply sql/init.sql. Idempotent."""
    config.validate()
    from calibration import db

    db.apply_schema()
    _print_block("Calibration tables", db.table_names() or ["(none found)"])
    return 0


def cmd_discover(args: argparse.Namespace) -> int:
    """Top the question pool up to its cohort targets."""
    config.validate()
    from calibration.polymarket import client, discover
    from calibration.repos import questions as questions_repo
    from calibration.services import discovery_service

    today = _parse_date(args.today)

    if args.dry_run:
        # Full fetch and full filter, zero writes. See the module docstring.
        markets = discovery_service.fetch_cohort_windows(today)
        candidates, rejections = discover.find_candidates(markets, today=today)
        open_counts = questions_repo.count_open_by_cohort()
        needed = discovery_service.compute_needed(open_counts)
        selected = discover.select_for_cohorts(candidates, needed)

        _print_block(
            "DRY RUN — nothing was written",
            [
                f"markets fetched : {len(markets)}",
                f"candidates      : {len(candidates)}",
                f"would insert    : {len(selected)}",
                "rejections      : "
                + ", ".join(f"{k}={v}" for k, v in sorted(rejections.items(), key=lambda kv: -kv[1])),
            ],
        )
        for candidate in selected:
            print(
                f"  [{candidate.cohort:>7}] [{candidate.category:<12}] "
                f"${candidate.volume_usd:>12,.0f}  {candidate.days_to_resolution:>2}d  "
                f"{candidate.question_text[:80]}"
            )
        print()
        return 0

    report = discovery_service.run_discovery(triggered_by="cli", today=today)
    _print_block("Discovery", report.summary_lines())
    return 0


def cmd_seed(args: argparse.Namespace) -> int:
    """
    Initial population.

    Identical to `discover` today — discovery is idempotent and additive, so
    seeding an empty database and topping up a populated one are the same
    operation. Kept as a separate command because the plan's acceptance
    criteria name it, and because `seed` communicates intent on first run.
    """
    return cmd_discover(args)


def cmd_resolve(args: argparse.Namespace) -> int:
    """Poll Polymarket for settled markets and record ground truth."""
    config.validate()
    from calibration.services import resolution_service

    report = resolution_service.resolve_open_questions(
        triggered_by="cli", days_ahead=args.days_ahead
    )
    _print_block("Resolution", report.summary_lines())
    for line in report.details:
        print(f"  {line}")
    if report.details:
        print()
    return 0


def cmd_list_questions(args: argparse.Namespace) -> int:
    """Tabular listing of tracked questions."""
    config.validate()
    from calibration.repos import questions as questions_repo

    rows = questions_repo.list_questions(
        status=args.status, cohort=args.cohort, category=args.category, limit=args.limit
    )
    if not rows:
        print("\nNo questions match. Run `discover` to populate.\n")
        return 0

    print(
        f"\n{'COHORT':<8} {'CATEGORY':<13} {'STATUS':<9} {'RESOLVES':<11} "
        f"{'SRC':<7} QUESTION"
    )
    print("-" * 110)
    for q in rows:
        print(
            f"{q.cohort:<8} {q.category:<13} {q.status:<9} "
            f"{q.expected_resolution_date.isoformat():<11} {q.added_by:<7} "
            f"{q.question_text[:60]}"
        )
    print(f"\n{len(rows)} question(s).\n")
    return 0


def cmd_add_manual(args: argparse.Namespace) -> int:
    """Add one question by Polymarket slug."""
    config.validate()
    from calibration.services import manual_add_service

    try:
        question, inserted_id = manual_add_service.add_manual_question(
            slug=args.slug,
            category=args.category,
            cohort=args.cohort,
            operator_email=args.operator,
            question_text=args.question_text,
        )
    except manual_add_service.ManualAddError as exc:
        print(f"\nCould not add: {exc}\n")
        return 1

    _print_block(
        "Added" if inserted_id else "Already tracked",
        [
            f"question   : {question.question_text[:80]}",
            f"slug       : {question.polymarket_slug}",
            f"condition  : {question.polymarket_condition_id}",
            f"cohort     : {question.cohort}",
            f"category   : {question.category}",
            f"resolves   : {question.expected_resolution_date.isoformat()}",
            f"url        : {question.polymarket_url}",
        ],
    )
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    """One-screen summary of the calibration database."""
    config.validate()
    from calibration.repos import forecasts as forecasts_repo
    from calibration.repos import questions as questions_repo
    from calibration.repos import resolutions as resolutions_repo

    by_status = questions_repo.count_by_status()
    by_cohort = questions_repo.count_open_by_cohort()
    outcomes = resolutions_repo.count_by_outcome()
    forecast_counts = forecasts_repo.count_by_status()

    _print_block(
        "Questions",
        [f"{k:<10}: {v}" for k, v in by_status.items()]
        + ["", "open by cohort:"]
        + [f"  {k:<8}: {v} / target {config.target_count_for(k)}" for k, v in by_cohort.items()],
    )
    _print_block("Resolutions", [f"{k:<10}: {v}" for k, v in outcomes.items()])
    # Every forecast status is printed, including the zeros and the failures.
    # See repos/forecasts.count_by_status for why.
    _print_block(
        "Forecasts (Phase 10B populates these)",
        [f"{k:<20}: {v}" for k, v in forecast_counts.items()],
    )
    return 0


def cmd_dispatch(args: argparse.Namespace) -> int:
    """Send open questions to the agent through Firestore."""
    config.validate()
    from calibration import firestore_client
    from calibration.services import dispatch_service

    if not firestore_client.is_emulator() and not args.allow_live:
        print(
            "\nFIRESTORE_EMULATOR_HOST is not set, so this would dispatch against "
            "LIVE Firestore.\nThat is gated behind --allow-live deliberately: a live "
            "dispatch creates real sessions\nand spends real tokens. Start the "
            "emulator, or pass --allow-live if you mean it.\n",
            file=sys.stderr,
        )
        return 4

    report = dispatch_service.dispatch_questions(
        question_ids=args.question_id or None,
        run_type=args.run_type,
        triggered_by="cli",
        purpose=args.purpose,
        evidence_caveat=args.evidence_caveat,
    )
    _print_block("Dispatch", report.summary_lines())
    if args.purpose or args.evidence_caveat:
        _print_block(
            "Stamped on this run",
            [f"purpose : {args.purpose or '—'}", f"caveat  : {args.evidence_caveat or '—'}"],
        )
    for session_id in report.session_ids:
        print(f"  {session_id}")
    if report.session_ids:
        print()
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    """Write the measurements out as CSV and JSON."""
    config.validate()
    from pathlib import Path

    from calibration.export import export_all

    target = Path(args.output)
    counts = export_all(target)

    _print_block(
        f"Exported to {target}",
        [f"{name:26} {n} row(s)" for name, n in counts.items()],
    )
    return 0


def cmd_cleanup(args: argparse.Namespace) -> int:
    """Sweep orphaned calibration sessions from Firestore."""
    config.validate()
    from calibration.services import cleanup_service

    report = cleanup_service.sweep(apply=args.apply)
    _print_block("Orphan cleanup", report.summary_lines())
    for session_id in report.orphan_ids:
        print(f"  {session_id}")
    if report.orphan_ids:
        print()
    return 0


def cmd_harvest(args: argparse.Namespace) -> int:
    """Collect results for forecasts still awaiting one."""
    config.validate()
    from calibration.services import harvest_service

    report = harvest_service.harvest_pending(triggered_by="cli")
    _print_block("Harvest", report.summary_lines())
    for line in report.details:
        print(f"  {line}")
    if report.details:
        print()
    return 0


def cmd_compute_metrics(args: argparse.Namespace) -> int:
    """Compute every metric and optionally persist a snapshot of each."""
    config.validate()
    from calibration.metrics import snapshots

    payloads = snapshots.compute_all()
    for line in snapshots.render_summary(payloads):
        print(line)

    if not args.no_write:
        written = snapshots.write_snapshots(payloads)
        _print_block("Snapshots written", [f"{k:<22}: {v}" for k, v in written.items()])
    else:
        print("\n(--no-write: nothing persisted)\n")
    return 0


def cmd_show_curve(_args: argparse.Namespace) -> int:
    config.validate()
    from calibration.metrics import calibration_curve, snapshots

    payload = snapshots.compute_all()["calibration_curve"]
    _print_block("Calibration curve", calibration_curve.render_ascii(payload))
    return 0


def cmd_show_cohort_brier(_args: argparse.Namespace) -> int:
    config.validate()
    from calibration.metrics import cohort_brier, snapshots

    payload = snapshots.compute_all()["cohort_brier"]
    _print_block("Cohort Brier", cohort_brier.render_ascii(payload))
    return 0


def cmd_show_improvement(_args: argparse.Namespace) -> int:
    config.validate()
    from calibration.metrics import improvement_curve, snapshots

    payload = snapshots.compute_all()["improvement_curve"]
    _print_block("Improvement curve", improvement_curve.render_ascii(payload))
    return 0


# ==========================================================
# Parser
# ==========================================================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m calibration.cli",
        description="Anizai calibration harness — Phase 10A (local only).",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("check-config", help="print and validate configuration").set_defaults(
        func=cmd_check_config
    )
    sub.add_parser("init-db", help="apply the calibration schema (idempotent)").set_defaults(
        func=cmd_init_db
    )
    sub.add_parser("status", help="summary of questions, resolutions, forecasts").set_defaults(
        func=cmd_status
    )

    for name, help_text in (
        ("discover", "top up the question pool to its cohort targets"),
        ("seed", "initial population (alias of discover)"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument(
            "--dry-run", action="store_true",
            help="fetch and filter but write nothing — use this first",
        )
        p.add_argument("--today", help="reference date YYYY-MM-DD (default: today UTC)")
        p.set_defaults(func=cmd_discover if name == "discover" else cmd_seed)

    p_resolve = sub.add_parser("resolve", help="poll Polymarket for settled markets")
    p_resolve.add_argument(
        "--days-ahead", type=int, default=2,
        help="also poll questions resolving within N days (default: 2)",
    )
    p_resolve.set_defaults(func=cmd_resolve)

    p_list = sub.add_parser("list-questions", help="list tracked questions")
    p_list.add_argument("--status", choices=["open", "resolved", "archived"])
    p_list.add_argument("--cohort", choices=["7d", "14d", "30-45d"])
    p_list.add_argument("--category", choices=["geopolitical", "financial", "ai", "other"])
    p_list.add_argument("--limit", type=int, default=200)
    p_list.set_defaults(func=cmd_list_questions)

    p_add = sub.add_parser("add-manual", help="add one question by Polymarket slug")
    p_add.add_argument("--slug", required=True, help="polymarket.com/event/<slug>")
    p_add.add_argument(
        "--category", required=True,
        choices=["geopolitical", "financial", "ai", "other"],
    )
    p_add.add_argument("--cohort", required=True, choices=["7d", "14d", "30-45d"])
    p_add.add_argument("--operator", required=True, help="operator email, for provenance")
    p_add.add_argument(
        "--question-text",
        help="override the market's question text (changes what is measured — "
             "use sparingly)",
    )
    p_add.set_defaults(func=cmd_add_manual)

    # --- Phase 10B: the Firestore bridge ---
    p_dispatch = sub.add_parser("dispatch", help="send open questions to the agent")
    p_dispatch.add_argument(
        "--question-id", action="append",
        help="dispatch only this question (repeatable). Default: every open question.",
    )
    p_dispatch.add_argument(
        "--run-type", default="manual",
        choices=["initial_seed", "weekly_reforecast", "manual", "single_question"],
    )
    p_dispatch.add_argument(
        "--allow-live", action="store_true",
        help="permit dispatching against live Firestore. Without the emulator "
             "running this is required, and it spends real tokens.",
    )
    p_dispatch.add_argument(
        "--purpose",
        help="what this run is FOR, stamped on the run row. A sanity check and "
             "a real measurement produce identical-looking rows; this is the "
             "only thing that tells them apart later.",
    )
    p_dispatch.add_argument(
        "--evidence-caveat",
        help="what is known to be wrong or partial about the agent's evidence "
             "right now (e.g. 'ingestion paused since 2026-07-23; momentum "
             "vault stale'). Stamped on the run row so the numbers can never "
             "be read without it.",
    )
    p_dispatch.set_defaults(func=cmd_dispatch)

    sub.add_parser("harvest", help="collect results for pending forecasts").set_defaults(
        func=cmd_harvest
    )

    p_export = sub.add_parser(
        "export", help="write the measurements out as CSV + JSON for submission"
    )
    p_export.add_argument(
        "--output", default="results",
        help="output directory (default: results/). Overwrites cleanly; "
             "everything is derived from the database.",
    )
    p_export.set_defaults(func=cmd_export)

    p_cleanup = sub.add_parser(
        "cleanup",
        help="sweep orphaned calibration sessions (dispatches that died mid-write)",
    )
    p_cleanup.add_argument(
        "--apply", action="store_true",
        help="actually delete. Without this the command only reports — a "
             "deletion tool that deletes by default eventually deletes "
             "something it should not.",
    )
    p_cleanup.set_defaults(func=cmd_cleanup)

    # --- Phase 10C: scoring and metrics ---
    p_metrics = sub.add_parser("compute-metrics", help="compute and snapshot all metrics")
    p_metrics.add_argument(
        "--no-write", action="store_true", help="print only; persist nothing"
    )
    p_metrics.set_defaults(func=cmd_compute_metrics)

    sub.add_parser("show-curve", help="print the calibration curve").set_defaults(
        func=cmd_show_curve
    )
    sub.add_parser("show-cohort-brier", help="print per-cohort Brier").set_defaults(
        func=cmd_show_cohort_brier
    )
    sub.add_parser("show-improvement", help="print the improvement curve").set_defaults(
        func=cmd_show_improvement
    )

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)
    try:
        return args.func(args)
    except ValueError as exc:
        # config.validate() failures land here — they are operator errors with
        # actionable messages, not stack traces.
        print(f"\nConfiguration error: {exc}\n", file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(f"\n{exc}\n", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
