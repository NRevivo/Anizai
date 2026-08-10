"""
Calibration — Phase 10 backtesting harness (Domain D).

A standalone system that measures how well-calibrated the Anizai agent's
forecasts are. It picks Polymarket questions with known future resolutions,
submits them to the *existing* agent through the *existing* Firestore flow,
waits for Polymarket to settle them, and scores the agent with Brier scores
and a calibration curve.

Isolation is the defining property of this package (see the plan §2.5,
non-negotiables N1-N7). Calibration is strictly additive:

    - It never imports `agent/`, `persistence/`, `processing/`, `ingestion/`,
      or `utils/`. Not for convenience, not for a "small helper". A shared
      import is a shared blast radius, and the whole premise of Phase 10 is
      that it measures production without perturbing it.
    - It has its own Postgres database, reached only via
      CALIBRATION_DATABASE_URL. It never touches the pipeline vaults.
    - It has its own config loader, its own DB layer, and its own HTTP client.

The duplication that isolation costs (a second connection-pool wrapper, a
second dotenv call) is deliberate and is cheaper than the coupling it avoids.
`tests/test_calibration/test_isolation.py` enforces this mechanically.

Sprint status: Phase 10A (Foundation) — local-only. This package currently has
NO Firestore surface and NO cloud surface at all; both arrive in Phase 10B and
Phase 10D respectively.

References:
    - data-pipeline/docs/D_calibration/calibration_plan.md §2.5 (non-negotiables)
    - data-pipeline/docs/D_calibration/calibration_plan.md §6 (Phase 10A scope)
"""

__version__ = "0.1.0-phase10a"
