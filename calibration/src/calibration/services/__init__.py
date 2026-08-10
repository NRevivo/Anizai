"""
Services — orchestration between the Polymarket adapter and the repositories.

Each service is the unit the CLI calls today and that a Cloud Run `/tasks/*`
endpoint will call in Phase 10D. Keeping the orchestration here rather than in
the CLI is what makes that later change a thin wrapper instead of a rewrite.
"""
