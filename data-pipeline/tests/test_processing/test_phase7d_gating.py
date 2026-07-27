"""
Gate 2 — Phase 7D Enrichment-Gating Invariance Suite (§4.3, §6).

The sprint's central assertion (§4.3): gating changes how many times an item is
ENRICHED — it must not change which items are ARCHIVED, which are captured as
rejects, or which pass the sniper. Only `llm_cost_events` row counts fall.

Why a STATEFUL fake vault (Ron directive, 2026-07-26): a fixed-return mock would
make "same archived set" hold vacuously — nothing is ever archived, so both runs
trivially archive the empty set. Here FakeKnowledgeVault / FakeSocialVault are real
accumulating structures: archive() inserts by hash and a repeat is a duplicate
*because the first delivery inserted it*, exactly like the production dedup guards
(F1 / F7). The gate decision under test is the REAL module-level
`dedup_skip_enrichment()` and the REAL `apply_rescue_outcome()`; only the DB and the
rescue/cosine embeddings are faked, and the rescue/promote outcome is injected per
doc so the suite isolates the GATE, not the embedding maths (that is proved intact,
unmodified, by test_semantic_rescue.py).

The two Flink process_element methods are defined inside `if PYFLINK_AVAILABLE:` and
are not importable in the test environment, so the harnesses below mirror their
control-flow order exactly — the same pattern as test_semantic_rescue.py's
TestDispatchCounts. Where a decision is a real extracted helper it is called
directly; the branch ORDER (which is what §4.3 and the named ordering assertion
turn on) is replicated faithfully and documented inline.

References:
    - docs/A_pipeline/plans/phase7d_enrichment_gating.md §4.3, §5 T3/T4/T6, §6
    - processing/gold_job.py: dedup_skip_enrichment, apply_rescue_outcome,
      GlobalNewsGoldFunction.process_element, PolymarketGoldSocialFunction.process_element
"""

from __future__ import annotations

import ast

import pytest

from processing.gold_job import dedup_skip_enrichment, apply_rescue_outcome


# ==========================================================
# Stateful fakes — the whole point of this suite
# ==========================================================

class FakeKnowledgeVault:
    """
    Accumulating stand-in for persistence.knowledge_vault.archive() (F1).

    archive() inserts a new document_hash and returns a fresh doc_id; a repeat of a
    hash already inserted returns None — the real dedup guard's discarded signal
    that the T3 gate reads. State lives across calls, so a "duplicate" in the stream
    is a duplicate ONLY because an earlier delivery inserted it.
    """

    def __init__(self) -> None:
        self.archived: dict[str, str] = {}   # document_hash -> doc_id
        self._n = 0

    def archive(self, silver_doc: dict):
        dh = silver_doc["document_hash"]
        if dh in self.archived:
            return None                      # duplicate — real archive() returns None
        self._n += 1
        doc_id = f"doc-{self._n}"
        self.archived[dh] = doc_id
        return doc_id


class FakeSocialVault:
    """
    Accumulating stand-in for persistence.social_vault (F7 — no UNIQUE constraint;
    idempotency is caller-managed via exists_by_content_hash()).

    exists_by_content_hash() is True once archive() has stored the content_hash. A
    social repeat is therefore a duplicate because a prior delivery archived it.
    """

    def __init__(self) -> None:
        self.archived: dict[str, str] = {}   # content_hash -> social_id
        self._n = 0

    def exists_by_content_hash(self, content_hash: str) -> bool:
        return content_hash in self.archived

    def archive(self, silver_social: dict) -> str:
        ch = silver_social.get("content_hash", "")
        # social_vault is append-only; the caller only archives when NOT already
        # present, so mirror that contract (a stray double-archive would be a caller
        # bug, not this fake's concern).
        if ch not in self.archived:
            self._n += 1
            self.archived[ch] = f"soc-{self._n}"
        return self.archived[ch]


# ==========================================================
# Harness — GlobalNewsGoldFunction.process_element, faithfully
# ==========================================================

def _run_global_news_stream(stream, *, gate_enabled, reject_capture=True):
    """
    Replay a stream of Silver docs through the global_news gate, mirroring
    GlobalNewsGoldFunction.process_element order:

        rescue block (low-signal only) -> kv_archive -> T3 dedup gate -> enrich

    Uses a fresh stateful FakeKnowledgeVault, the REAL apply_rescue_outcome (which
    captures rejects + mutates the doc on promote), and the REAL
    dedup_skip_enrichment. insert_reject is intercepted so no DB is needed.

    Each stream item is a dict:
        document_hash, canonical_event_id, is_high_signal,
        and (low-signal only) rescue=(rescued: bool, similarity: float)

    Returns a dict:
        archived  -> set of archived document_hashes
        rejects   -> list of (canonical_event_id, cosine) in capture order
        sniper    -> list of is_high_signal decisions at stream-entry (order-preserving)
        llm_calls -> number of enrichment calls (the only thing the gate may change)
    """
    import persistence.filter_rejects as fr

    vault = FakeKnowledgeVault()
    rejects: list[tuple] = []
    sniper: list[bool] = []
    llm_calls = 0

    # Intercept insert_reject (apply_rescue_outcome lazy-imports it from the module).
    orig_insert = fr.insert_reject

    def _capture(silver_doc, rescue_cosine, run_id=None, canonical_event_id=None):
        rejects.append((canonical_event_id, rescue_cosine))
        return "reject-id"

    fr.insert_reject = _capture
    try:
        for item in stream:
            doc = dict(item)                     # fresh copy per delivery (as Flink deserialises)
            is_high = doc.get("is_high_signal", False)
            sniper.append(is_high)

            # --- Stage 2: semantic rescue (low-signal only). apply_rescue_outcome
            #     is the REAL extracted function; the rescue OUTCOME is injected so
            #     this suite isolates the gate, not the embedding. Runs BEFORE the
            #     T3 gate, exactly as in process_element. ---
            if not is_high:
                rescued, similarity = doc.get("rescue", (False, 0.0))
                survived = apply_rescue_outcome(
                    doc, rescued, similarity,
                    capture_enabled=reject_capture, run_id="test-run",
                )
                if not survived:
                    continue                     # dropped: reject captured, no archive, no Gold

            # --- kv_archive (stateful): duplicate -> None, no re-insert ---
            doc_id = vault.archive(doc)
            archive_raised = False

            # --- T3 gate (REAL helper): skip enrichment on a known duplicate ---
            if dedup_skip_enrichment(doc_id, archive_raised, gate_enabled=gate_enabled):
                continue

            # --- dispatch -> enrichment (the ONE thing the gate may reduce) ---
            llm_calls += 1
    finally:
        fr.insert_reject = orig_insert

    return {
        "archived": set(vault.archived.keys()),
        "rejects":  rejects,
        "sniper":   sniper,
        "llm_calls": llm_calls,
    }


def _news_stream():
    """
    A stream containing genuine first-occurrences AND genuine repeats across every
    path the gate touches: high-signal new+repeat, low-signal drop new+repeat (never
    archived), and low-signal PROMOTE new+repeat (archived, then a real duplicate).
    """
    return [
        {"document_hash": "a" * 64, "canonical_event_id": "evt-a", "is_high_signal": True},
        {"document_hash": "a" * 64, "canonical_event_id": "evt-a", "is_high_signal": True},   # repeat of a
        {"document_hash": "b" * 64, "canonical_event_id": "evt-b", "is_high_signal": True},
        {"document_hash": "c" * 64, "canonical_event_id": "evt-c", "is_high_signal": False,
         "rescue": (False, 0.12)},                                                            # drop
        {"document_hash": "c" * 64, "canonical_event_id": "evt-c", "is_high_signal": False,
         "rescue": (False, 0.12)},                                                            # drop repeat
        {"document_hash": "d" * 64, "canonical_event_id": "evt-d", "is_high_signal": False,
         "rescue": (True, 0.41)},                                                             # promote -> archive
        {"document_hash": "d" * 64, "canonical_event_id": "evt-d", "is_high_signal": False,
         "rescue": (True, 0.41)},                                                             # promote repeat -> dup
    ]


# ==========================================================
# §4.3 — the central invariance assertion
# ==========================================================

class TestGlobalNewsInvariance:
    """Gate ON vs OFF over the identical stream, each against a FRESH stateful vault."""

    def test_archived_set_reject_set_and_sniper_are_identical(self):
        on  = _run_global_news_stream(_news_stream(), gate_enabled=True)
        off = _run_global_news_stream(_news_stream(), gate_enabled=False)

        assert on["archived"] == off["archived"], (
            "The gate must not change which document_hashes are archived"
        )
        assert on["archived"] == {"a" * 64, "b" * 64, "d" * 64}, (
            "c is low-signal-dropped (never archived); a/b high-signal and d promoted are"
        )
        assert on["rejects"] == off["rejects"], (
            "The gate must not change filter_rejects content"
        )
        assert on["sniper"] == off["sniper"], (
            "The gate must not change sniper pass/fail decisions"
        )

    def test_gate_on_strictly_fewer_llm_calls(self):
        on  = _run_global_news_stream(_news_stream(), gate_enabled=True)
        off = _run_global_news_stream(_news_stream(), gate_enabled=False)

        # OFF: a, a-repeat, b, d, d-repeat = 5 enrichments (c dropped both times).
        # ON:  a, b, d = 3 (the two repeats of a and d are skipped as duplicates).
        assert off["llm_calls"] == 5
        assert on["llm_calls"] == 3
        assert on["llm_calls"] < off["llm_calls"], "Gate ON must enrich strictly fewer items"

    def test_first_occurrence_is_enriched_with_gate_on(self):
        """The gate must not suppress genuine new content — the over-aggressive-key guard."""
        first_only = [
            {"document_hash": "z" * 64, "canonical_event_id": "evt-z", "is_high_signal": True},
        ]
        on = _run_global_news_stream(first_only, gate_enabled=True)
        assert on["llm_calls"] == 1, "A first occurrence must be enriched even with the gate ON"
        assert on["archived"] == {"z" * 64}

    def test_duplicate_rejects_captured_the_same_with_gate_on_and_off(self):
        """
        A low-signal DROP repeat (c, delivered twice) captures a reject on each
        delivery, gate ON and OFF alike — the rescue/capture is upstream of the T3
        gate, so toggling the gate cannot starve the reject corpus.
        """
        on  = _run_global_news_stream(_news_stream(), gate_enabled=True)
        off = _run_global_news_stream(_news_stream(), gate_enabled=False)
        c_rejects_on  = [r for r in on["rejects"] if r[0] == "evt-c"]
        c_rejects_off = [r for r in off["rejects"] if r[0] == "evt-c"]
        assert len(c_rejects_on) == 2
        assert c_rejects_on == c_rejects_off


# ==========================================================
# Gate 2 — T3 duplicate-vs-archive_raised distinction (D3)
# Both branches asserted in the SAME class so a refactor cannot collapse them.
# ==========================================================

class TestDedupGateNoneDistinction:
    """
    dedup_skip_enrichment must treat the two doc_id=None cases oppositely:
      (i)  duplicate      (archive returned None, no raise) -> skip
      (ii) archive raised (caught; doc_id left None)        -> PROCEED (fail-open)
    """

    def test_duplicate_none_without_raise_skips(self):
        assert dedup_skip_enrichment(None, False, gate_enabled=True) is True

    def test_archive_raised_none_proceeds(self):
        # Same None doc_id, but archive_raised=True — a DB blip must never be read
        # as a duplicate, or a transient outage silently stops ingestion.
        assert dedup_skip_enrichment(None, True, gate_enabled=True) is False

    def test_new_article_doc_id_proceeds(self):
        assert dedup_skip_enrichment("doc-1", False, gate_enabled=True) is False

    def test_gate_off_never_skips_even_a_duplicate(self):
        assert dedup_skip_enrichment(None, False, gate_enabled=False) is False

    def test_gate_off_with_raise_proceeds(self):
        assert dedup_skip_enrichment(None, True, gate_enabled=False) is False

    def test_archive_raised_via_stateful_vault_is_not_a_duplicate(self):
        """
        End-to-end with the stateful vault: a first delivery whose archive() RAISES
        must still be enriched (fail-open) AND leave the vault empty — proving the
        gate did not mistake the raise for a duplicate.
        """
        class _RaisingVault(FakeKnowledgeVault):
            def archive(self, silver_doc):
                raise RuntimeError("connection refused")

        vault = _RaisingVault()
        doc = {"document_hash": "e" * 64, "canonical_event_id": "evt-e", "is_high_signal": True}
        try:
            doc_id = vault.archive(doc)
            raised = False
        except Exception:
            doc_id = None
            raised = True

        assert raised is True
        assert dedup_skip_enrichment(doc_id, raised, gate_enabled=True) is False
        assert vault.archived == {}, "A raised archive stores nothing"


# ==========================================================
# Harness — PolymarketGoldSocialFunction.process_element, faithfully
# ==========================================================

def _run_social_hn_stream(stream, *, gate_enabled, reject_capture=True):
    """
    Replay a stream of HackerNews Silver social records through the social gate,
    mirroring PolymarketGoldSocialFunction.process_element order:

        exists_by_content_hash -> archive-if-new
        -> [HN] low-signal reject capture (BEFORE the gate)
        -> [HN] high-signal dedup gate
        -> enrich

    The ORDER is the whole point of the named invariance assertion: the low-signal
    capture is textually and executionally ahead of the is_high_signal-guarded gate,
    so a low-signal duplicate is captured whether the gate is on or off. Uses a fresh
    stateful FakeSocialVault; the reject cosine is injected (compute_social_rescue_cosine
    is unit-tested elsewhere) so this isolates the ordering/gate.

    Stream item: content_hash, canonical_event_id, is_high_signal, cosine (low-signal).

    Returns: archived (set of content_hashes), rejects (list of (cei, cosine)),
             consensus_calls (int).
    """
    vault = FakeSocialVault()
    rejects: list[tuple] = []
    consensus_calls = 0

    for item in stream:
        doc = dict(item)
        content_hash = doc.get("content_hash", "")
        is_high = doc.get("is_high_signal", False)

        already_archived = bool(content_hash) and vault.exists_by_content_hash(content_hash)
        if not already_archived:
            vault.archive(doc)                    # archive NEW (incl. low-signal HN)

        # --- HN low-signal reject capture — BEFORE the gate (invariance §6) ---
        if not is_high:
            if reject_capture:
                rejects.append((doc.get("canonical_event_id"), doc.get("cosine", 0.0)))
            continue

        # --- HN high-signal dedup gate — is_high_signal-guarded ---
        if gate_enabled and already_archived:
            continue                              # skip consensus enrichment

        consensus_calls += 1

    return {
        "archived": set(vault.archived.keys()),
        "rejects":  rejects,
        "consensus_calls": consensus_calls,
    }


class TestHackerNewsSocialInvariance:
    """The social half of §4.3, on a stateful FakeSocialVault."""

    def test_named_low_signal_hn_duplicate_captured_identically_gate_on_off(self):
        """
        NAMED INVARIANCE ASSERTION (plan §6). A HackerNews story that is BOTH
        low-signal AND a dedup duplicate must produce an identical filter_rejects
        row with the gate ON and with the gate OFF.

        Why the ordering matters: this holds ONLY because the T6 capture branch runs
        BEFORE the T4 dedup gate, and the gate is is_high_signal-guarded. If the gate
        were ever reordered ahead of the capture (or lost its is_high_signal guard),
        a low-signal duplicate would be skipped before capture with the gate ON but
        captured with it OFF — silently starving the 7B.5 corpus and passing every
        count-based check. This test is the tripwire for exactly that regression.
        """
        stream = [
            {"content_hash": "h" * 64, "canonical_event_id": "evt-h",
             "is_high_signal": False, "cosine": 0.20},
            {"content_hash": "h" * 64, "canonical_event_id": "evt-h",
             "is_high_signal": False, "cosine": 0.20},   # duplicate low-signal HN
        ]
        on  = _run_social_hn_stream(stream, gate_enabled=True)
        off = _run_social_hn_stream(stream, gate_enabled=False)

        assert on["rejects"] == off["rejects"], (
            "Low-signal HN duplicate: reject content must be identical gate ON vs OFF"
        )
        assert len(on["rejects"]) == 2, "Each delivery of the low-signal story is captured"
        assert on["consensus_calls"] == 0, "Low-signal HN is never enriched"

    def test_high_signal_hn_duplicate_gate_skips_but_archive_and_capture_unchanged(self):
        """
        A high-signal HN duplicate: the gate reduces consensus calls (ON<OFF) while
        the social_vault archived set and the reject set stay identical.
        """
        stream = [
            {"content_hash": "k" * 64, "canonical_event_id": "evt-k", "is_high_signal": True},
            {"content_hash": "k" * 64, "canonical_event_id": "evt-k", "is_high_signal": True},  # dup
        ]
        on  = _run_social_hn_stream(stream, gate_enabled=True)
        off = _run_social_hn_stream(stream, gate_enabled=False)

        assert on["archived"] == off["archived"] == {"k" * 64}
        assert on["rejects"] == off["rejects"] == []
        assert off["consensus_calls"] == 2
        assert on["consensus_calls"] == 1
        assert on["consensus_calls"] < off["consensus_calls"]

    def test_low_signal_hn_first_delivery_is_archived(self):
        """Invariant: a NEW low-signal HN story is still archived to social_vault."""
        stream = [
            {"content_hash": "m" * 64, "canonical_event_id": "evt-m",
             "is_high_signal": False, "cosine": 0.1},
        ]
        res = _run_social_hn_stream(stream, gate_enabled=True)
        assert res["archived"] == {"m" * 64}
        assert len(res["rejects"]) == 1

    def test_low_signal_hn_capture_flag_off_writes_no_reject(self):
        """REJECT_CAPTURE_ENABLED=false → a dropped low-signal HN story writes no
        reject row; the drop is otherwise identical (still not enriched)."""
        stream = [
            {"content_hash": "n" * 64, "canonical_event_id": "evt-n",
             "is_high_signal": False, "cosine": 0.1},
        ]
        on  = _run_social_hn_stream(stream, gate_enabled=True, reject_capture=True)
        off = _run_social_hn_stream(stream, gate_enabled=True, reject_capture=False)
        assert len(on["rejects"]) == 1
        assert off["rejects"] == []
        assert on["consensus_calls"] == off["consensus_calls"] == 0


# ==========================================================
# PRODUCTION AST guard — the harness above proves the CONTRACT; this proves
# PolymarketGoldSocialFunction.process_element still enforces it (Ron directive).
# ==========================================================

class TestSocialGateOrderingIsProductionEnforced:
    """
    The named low-signal-HN-duplicate test asserts the ORDERING via the harness. A
    harness cannot notice if PRODUCTION is reordered. This AST guard reads
    processing.gold_job and asserts, inside PolymarketGoldSocialFunction.process_element:

      1. the T6 insert_reject (capture) call PRECEDES the T4 dedup-gate branch, and
      2. the dedup gate sits under an is_high_signal guard — a `if not is_high: ...
         return` low-signal early-exit ahead of it — so a low-signal story can never
         reach the gate.

    Either property lost (gate moved ahead of capture, or the is_high guard dropped)
    would let the gate skip a low-signal duplicate before capture with the flag ON,
    silently starving the 7B.5 corpus. This test fails the moment that happens.
    """

    def _process_element(self):
        import ast
        import inspect
        import processing.gold_job as gj

        tree = ast.parse(inspect.getsource(gj))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "PolymarketGoldSocialFunction":
                for m in node.body:
                    if isinstance(m, ast.FunctionDef) and m.name == "process_element":
                        return m
        raise AssertionError("PolymarketGoldSocialFunction.process_element not found")

    def test_capture_precedes_gate_and_gate_under_is_high_guard(self):
        method = self._process_element()

        # (a) the single insert_reject capture call
        capture_lines = [
            n.lineno for n in ast.walk(method)
            if isinstance(n, ast.Call)
            and (getattr(n.func, "id", "") == "insert_reject"
                 or getattr(n.func, "attr", "") == "insert_reject")
        ]
        assert len(capture_lines) == 1, (
            f"expected exactly one insert_reject in process_element, got {capture_lines}"
        )
        capture_line = capture_lines[0]

        # (b) the dedup-gate branch: an `if` whose test references self._dedup_gate_enabled
        gate_lines = [
            n.lineno for n in ast.walk(method)
            if isinstance(n, ast.If)
            and any(getattr(a, "attr", "") == "_dedup_gate_enabled" for a in ast.walk(n.test))
        ]
        assert gate_lines, "no self._dedup_gate_enabled gate branch in process_element"
        gate_line = min(gate_lines)

        # (c) the is_high_signal guard: `if not is_high: ... return`
        guard_lines = [
            n.lineno for n in ast.walk(method)
            if isinstance(n, ast.If)
            and isinstance(n.test, ast.UnaryOp) and isinstance(n.test.op, ast.Not)
            and isinstance(n.test.operand, ast.Name) and n.test.operand.id == "is_high"
            and any(isinstance(x, ast.Return) for x in ast.walk(n))
        ]
        assert guard_lines, "no `if not is_high: ... return` low-signal guard in process_element"
        guard_line = min(guard_lines)

        assert guard_line < capture_line, "capture must live inside the low-signal (not is_high) branch"
        assert capture_line < gate_line, "insert_reject (capture) must PRECEDE the dedup gate"
        assert guard_line < gate_line, "the is_high_signal guard must gate (precede) the dedup branch"

