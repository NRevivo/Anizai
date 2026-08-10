"""
Isolation guard — the machine-checkable form of the plan's red lines.

The whole premise of Phase 10 is that it measures production without
perturbing it (plan §2.5, N1-N7). That guarantee is currently held up by
discipline and code review, both of which decay. This module turns it into a
test that fails loudly the first time someone reaches for a convenient import.

It parses the calibration package's imports statically rather than importing
the modules and inspecting sys.modules, so a forbidden import is caught even
if the module in question is never executed by any other test.

If a future sprint genuinely needs one of these, the fix is to change the plan
and this list in the same commit — not to quietly add the import.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

CALIBRATION_ROOT = Path(__file__).resolve().parents[2] / "src" / "calibration"

# Top-level packages calibration must never import (plan §2.5 N1-N4).
#
# `utils` is on the list despite not being named in N1-N4: `utils/__init__.py`
# imports `utils.db` unconditionally, which reads the pipeline's POSTGRES_*
# settings. Importing anything from `utils` therefore opens a path to the
# vault database, which is precisely what N4 forbids.
#
# `config` is on the list because `config/settings.py` has import-time side
# effects (creates DATA_DIR, loads the pipeline .env) and exposes the vault
# connection constants. calibration/config.py replaces it deliberately.
FORBIDDEN_TOP_LEVEL = frozenset({
    "agent",
    "persistence",
    "processing",
    "ingestion",
    "orchestration",
    "utils",
    "config",
})


def _python_files() -> list[Path]:
    return sorted(CALIBRATION_ROOT.rglob("*.py"))


def _imported_top_levels(path: Path) -> set[str]:
    """Every top-level package name imported by one file."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom):
            # node.level > 0 is a relative import — cannot escape the package.
            if node.level == 0 and node.module:
                names.add(node.module.split(".", 1)[0])
    return names


def test_calibration_package_exists():
    assert CALIBRATION_ROOT.is_dir(), "calibration package is missing"
    assert _python_files(), "calibration package contains no Python files"


@pytest.mark.parametrize("path", _python_files(), ids=lambda p: p.name)
def test_no_forbidden_imports(path: Path):
    """
    No calibration module may import the agent, the pipeline, or their shared
    utilities. See the module docstring.
    """
    offending = _imported_top_levels(path) & FORBIDDEN_TOP_LEVEL
    assert not offending, (
        f"{path.relative_to(CALIBRATION_ROOT.parent)} imports {sorted(offending)}, "
        f"which the calibration package is forbidden from touching (plan §2.5 "
        f"N1-N4). Calibration must own its equivalent."
    )


# The single module permitted to import a Firebase/Google SDK.
#
# Phase 10A had no Firestore surface at all and this test asserted exactly
# that. Phase 10B introduced one, and this assertion tightened rather than
# relaxed: Firestore access is now confined to one file. Concentrating it
# there is what makes "what can this code touch" answerable by reading a
# single module instead of auditing the package.
FIRESTORE_GATEWAY = "firestore_client.py"

# `auth.py` verifies Firebase ID tokens, which needs `firebase_admin.auth`.
# That is Firebase *Auth*, not Firestore — it reads no documents and touches
# no collection. The rule being enforced is about Firestore data access, so
# the exemption is narrow and the assertion below still checks that auth.py
# performs no document operations.
FIREBASE_AUTH_ONLY = {"auth.py"}


def test_only_the_gateway_module_performs_firestore_operations():
    """
    No calibration module except the gateway may read or write a document.

    A service that reached Firestore directly could issue a collection scan or
    a query, and a query is the one operation that can return a document
    calibration did not create. The gateway exposes only document-by-id reads
    and the two dispatch writes, so confining data access to it is what makes
    the N5/N6 guarantee structural rather than a matter of care.

    Checked on operations rather than imports: `firebase_admin` also carries
    Auth, which `auth.py` legitimately needs, and a guard that banned the
    whole package would have to be either wrong or disabled. Banning the
    operations is both narrower and closer to what actually matters.
    """
    offenders: list[str] = []

    for path in _python_files():
        if path.name == FIRESTORE_GATEWAY:
            continue
        source = path.read_text(encoding="utf-8")
        code = "\n".join(
            line for line in source.splitlines() if not line.strip().startswith("#")
        )
        import re

        code = re.sub(r'"""[\s\S]*?"""', "", code)

        for operation in (".collection(", "firestore.client(", ".document("):
            if operation in code:
                offenders.append(f"{path.name}: {operation}")

    assert not offenders, (
        f"{offenders} perform Firestore document operations. Only "
        f"{FIRESTORE_GATEWAY} may — route the access through it so every "
        "Firestore operation calibration performs stays enumerable in one file "
        "(plan §2.5 N5/N6)."
    )


def test_firebase_sdk_imports_stay_confined_to_two_modules():
    """
    The gateway may import Firestore; `auth.py` may import Firebase Auth.
    Nothing else imports a Firebase SDK at all.
    """
    allowed = {FIRESTORE_GATEWAY} | FIREBASE_AUTH_ONLY
    offenders = [
        path.name
        for path in _python_files()
        if path.name not in allowed
        and ({"firebase_admin", "google"} & _imported_top_levels(path))
    ]

    assert not offenders, (
        f"{offenders} import a Firebase/Google SDK. Only {sorted(allowed)} may."
    )


def _gateway_code() -> str:
    """The gateway's source with comments and docstrings stripped."""
    import re

    source = (CALIBRATION_ROOT / FIRESTORE_GATEWAY).read_text(encoding="utf-8")
    code = "\n".join(
        line for line in source.splitlines() if not line.strip().startswith("#")
    )
    return re.sub(r'"""[\s\S]*?"""', "", code)


def test_every_gateway_query_is_scoped_to_the_calibration_user():
    """
    The gateway is allowed exactly one kind of query, and only that kind.

    Almost every read here is a document lookup by an id we minted, which is
    what makes it structurally impossible to reach anyone else's data. Orphan
    cleanup breaks that pattern of necessity — an orphan has no Postgres row,
    so there is no id to look up — and it is the only exception.

    The compensating control is that the filter runs server-side on `userId`.
    A `.where()` on anything else, or an unfiltered collection read, would
    quietly widen the blast radius to real users' sessions while continuing to
    work perfectly on calibration's own data. That is why this is asserted
    rather than trusted.
    """
    import re

    code = _gateway_code()
    queries = list(re.finditer(r"\.where\(", code))

    assert len(queries) <= 1, (
        f"firestore_client.py issues {len(queries)} queries. Cleanup needs one; "
        "everything else must read documents by id (plan §2.5 N6)."
    )

    for match in queries:
        window = code[match.start():match.start() + 300]
        assert "userId" in window and "CALIBRATION_USER_ID" in window, (
            "A Firestore query is not scoped to CALIBRATION_USER_ID. Every "
            "query must filter server-side on the sentinel user, so documents "
            "belonging to real users are never transmitted (plan §2.5 N6)."
        )


def test_collection_streams_are_scoped(monkeypatch):
    """
    Every `.stream()` must be reached through either `.document(...)` — a
    subcollection of a session we own — or a `.where()` on the sentinel user.
    """
    import re

    code = _gateway_code()
    for match in re.finditer(r"\.stream\(\)", code):
        window = code[max(0, match.start() - 400):match.start()]
        assert ".document(" in window or "CALIBRATION_USER_ID" in window, (
            "firestore_client.py streams a collection that is scoped neither "
            "to a document we own nor to the calibration user."
        )


def test_deletion_refuses_ids_that_are_not_ours():
    """
    The last gate before an irreversible operation on a shared collection.

    Redundant with the `userId` scoping by design — this is the check that
    still holds if a future refactor loosens the query.
    """
    import sys

    sys.path.insert(0, str(CALIBRATION_ROOT.parent))
    from calibration import firestore_client

    for foreign_id in ("real_user_session_abc", "", "session-123", "CAL_upper"):
        with pytest.raises(ValueError, match="Refusing to delete"):
            firestore_client.delete_calibration_session(foreign_id)


def test_no_writes_outside_calibration_tables():
    """
    Every INSERT/UPDATE/DELETE in the package targets a `calibration_` table.

    A crude check — it scans SQL string literals for write verbs and asserts
    the following identifier carries the prefix. Crude is the point: it costs
    nothing and it would catch the specific catastrophic typo (a calibration
    migration pointed at a vault table) that no amount of care reliably
    prevents.
    """
    import re

    write_pattern = re.compile(
        r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+([a-zA-Z_][a-zA-Z0-9_]*)",
        re.IGNORECASE,
    )
    violations: list[str] = []

    for path in _python_files():
        for verb, table in write_pattern.findall(path.read_text(encoding="utf-8")):
            if not table.lower().startswith("calibration_"):
                violations.append(f"{path.name}: {verb} {table}")

    assert not violations, (
        "Calibration writes to non-calibration tables: "
        f"{violations}. Only `calibration_*` tables may be written (plan §2.5 N4)."
    )
