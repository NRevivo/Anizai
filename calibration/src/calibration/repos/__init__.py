"""
Repositories — the only modules that write SQL for the calibration database.

One module per table. Everything above this layer works with the Pydantic
models in `calibration/models.py` and never composes a query. This mirrors the
pipeline's Service Isolation convention (only `persistence/` touches the
vaults) applied to calibration's own database.
"""
