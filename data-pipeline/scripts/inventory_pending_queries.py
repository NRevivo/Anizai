"""
Diagnostic — inventory forecastQueries by status (and the 4 orphan docs
the worker touched on the failed boot).

Read-only. Writes nothing. Used for T20.11 diagnosis after the orphan-doc
KeyError surfaced.
"""

from __future__ import annotations

import sys

from agent.firestore_client import get_db, init_app

ORPHANS = [
    "RWEBcc2cH36HzYlsk2bl",
    "V68rEQPZYJARvHhiDCB9",
    "mW7U8RLV8ppB3cZ5Pjs3",
    "yQtuLKR3ZctQrpVFhqoT",
]


def main() -> int:
    init_app()
    db = get_db()

    # 1. Status counts across forecastQueries
    print("=== forecastQueries by status ===")
    counts: dict[str, int] = {}
    for snap in db.collection("forecastQueries").stream():
        s = (snap.to_dict() or {}).get("status", "<missing>")
        counts[s] = counts.get(s, 0) + 1
    for s, c in sorted(counts.items()):
        print(f"  {s}: {c}")

    # 2. State of the 4 orphans we touched
    print()
    print("=== orphan doc state after worker run ===")
    for doc_id in ORPHANS:
        fq_snap = db.collection("forecastQueries").document(doc_id).get()
        sess_snap = db.collection("sessions").document(doc_id).get()
        result_snap = db.collection("sessionResults").document(doc_id).get()
        fq = fq_snap.to_dict() if fq_snap.exists else None
        sess = sess_snap.to_dict() if sess_snap.exists else None
        result = result_snap.to_dict() if result_snap.exists else None
        print(f"  {doc_id}:")
        if fq is None:
            print("    forecastQueries: <doc missing>")
        else:
            keys = sorted(fq.keys())
            print(
                f"    forecastQueries: status={fq.get('status')!r} "
                f"errorMessage={fq.get('errorMessage', '')[:80]!r} "
                f"keys={keys}"
            )
        if sess is None:
            print("    sessions       : <doc missing>")
        else:
            print(
                f"    sessions       : status={sess.get('status')!r} "
                f"errorCode={sess.get('errorCode')!r}"
            )
        print(f"    sessionResults : {'EXISTS' if result else 'absent'}")

    # 3. Are there any remaining pending docs that would re-fire on next start?
    print()
    print("=== current pending docs (would re-fire on worker start) ===")
    pending = db.collection("forecastQueries").where(
        "status", "==", "pending"
    ).stream()
    pending_ids: list[str] = [s.id for s in pending]
    if not pending_ids:
        print("  none — clean to restart worker")
    else:
        for pid in pending_ids:
            print(f"  {pid}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
