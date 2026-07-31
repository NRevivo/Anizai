"""
Auth — Firebase ID token verification plus an operator email allowlist.

Two gates on `/api/*`, and both must pass:

    1. The bearer token verifies against Firebase Auth.
    2. The verified email is on `FIREBASE_AUTH_OPERATOR_EMAILS`.

The allowlist is the load-bearing one. Firebase Auth on the `anizai-ai`
project will happily issue a valid token to any user who signs up for the
product — so verification alone proves "a real user", not "an operator". The
dashboard exposes the agent's failure rate, per-question forecasts, and a
manual-add form; that is internal, not customer-facing.

The allowlist is read from the environment on every request rather than cached
at import. Revoking an operator should take effect when the config changes,
not at the next deploy — and the read is a dict lookup, so there is nothing to
optimise.

Emails are compared case-insensitively and trimmed. `Ron@Example.com ` in the
secret and `ron@example.com` in the token are the same operator, and a
whitespace-sensitive comparison would fail in a way that looks like a
permissions bug.

References:
    - calibration_plan.md §3 G7 (allowlist), §10 T10E.1
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

ALLOWLIST_ENV = "FIREBASE_AUTH_OPERATOR_EMAILS"

# Set to "1" to accept a bare email as a bearer token. Emulator and local
# development only — it is refused whenever FIRESTORE_EMULATOR_HOST is unset,
# so it cannot be switched on by accident against a live project.
DEV_AUTH_ENV = "CALIBRATION_DEV_AUTH"


class AuthError(Exception):
    """Raised with an HTTP status and an operator-readable reason."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def normalize_email(email: str) -> str:
    return email.strip().lower()


def operator_allowlist() -> set[str]:
    """
    The current allowlist, read fresh from the environment.

    An empty allowlist denies everyone. That is deliberate: the alternative —
    an empty list meaning "allow all" — turns a misconfigured or missing
    secret into an open dashboard, which is the worst possible direction for
    this particular failure to point.
    """
    raw = os.getenv(ALLOWLIST_ENV, "")
    return {normalize_email(part) for part in raw.split(",") if part.strip()}


def _dev_auth_enabled() -> bool:
    """
    Dev auth is only ever available against the emulator.

    Double-gated on purpose: the flag alone is not enough. A developer who
    exports CALIBRATION_DEV_AUTH and later points at a live project does not
    silently end up with an unauthenticated API.
    """
    if os.getenv(DEV_AUTH_ENV, "") != "1":
        return False
    if not os.getenv("FIRESTORE_EMULATOR_HOST"):
        logger.error(
            "[auth] %s is set but FIRESTORE_EMULATOR_HOST is not — refusing to "
            "enable dev auth against a non-emulator project.", DEV_AUTH_ENV,
        )
        return False
    return True


def verify_token(token: str) -> str:
    """
    Verify a Firebase ID token and return its email.

    Raises:
        AuthError(401): token missing, malformed, expired, or unverifiable.
    """
    if not token:
        raise AuthError(401, "Missing bearer token")

    if _dev_auth_enabled():
        if "@" not in token:
            raise AuthError(401, "Dev auth expects a bare email as the bearer token")
        return normalize_email(token)

    try:
        from firebase_admin import auth as fb_auth

        from calibration import firestore_client

        firestore_client.init_app()
        decoded = fb_auth.verify_id_token(token)
    except AuthError:
        raise
    except Exception as exc:  # noqa: BLE001 — every verification failure is a 401
        logger.warning("[auth] Token verification failed: %s", exc)
        raise AuthError(401, "Invalid or expired token") from exc

    email = decoded.get("email")
    if not email:
        # A token without an email cannot be checked against the allowlist, so
        # it cannot be authorised however valid it is.
        raise AuthError(401, "Token carries no email claim")
    return normalize_email(email)


def authorize(email: str) -> str:
    """
    Check a verified email against the allowlist.

    Raises:
        AuthError(403): the token is valid but this person is not an operator.
    """
    allowlist = operator_allowlist()

    if not allowlist:
        logger.error(
            "[auth] %s is empty — denying every request. Set the allowlist to "
            "grant access.", ALLOWLIST_ENV,
        )
        raise AuthError(403, "No operators are configured")

    if normalize_email(email) not in allowlist:
        logger.warning("[auth] Rejected non-operator %s", email)
        raise AuthError(403, "Not an authorised operator")

    return normalize_email(email)


def verify_scheduler_token(
    authorization_header: Optional[str], expected_audience: Optional[str] = None
) -> str:
    """
    Verify an OIDC token from Cloud Scheduler, guarding the `/tasks/*` routes.

    Returns the calling service account's email.

    Two checks, and the second is the one that matters. Verifying the
    signature only proves Google issued the token — Google issues tokens to
    everyone. The **audience** proves it was minted for *this service*, and
    the **issuer email** proves it came from our scheduler rather than any
    other principal who can obtain a Google token. Checking the signature
    alone would leave `/tasks/dispatch` callable by any Google account on the
    internet, and that endpoint spends money.

    Raises:
        AuthError(401): missing, malformed, or unverifiable token.
        AuthError(403): valid token from a principal we do not accept.
    """
    if not authorization_header:
        raise AuthError(401, "Missing Authorization header")

    scheme, _, token = authorization_header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise AuthError(401, "Authorization header must be 'Bearer <token>'")

    audience = expected_audience or os.getenv("CALIBRATION_OIDC_AUDIENCE", "")
    allowed = {
        normalize_email(sa)
        for sa in os.getenv("CALIBRATION_SCHEDULER_SERVICE_ACCOUNTS", "").split(",")
        if sa.strip()
    }

    if not allowed:
        # The same fail-closed rule as the operator allowlist. An unset
        # variable must not mean "accept any caller" on the endpoint that
        # dispatches forecasts.
        logger.error(
            "[auth] CALIBRATION_SCHEDULER_SERVICE_ACCOUNTS is empty — refusing "
            "every /tasks/* call. Set it to the scheduler's service account."
        )
        raise AuthError(403, "No scheduler principals are configured")

    try:
        from google.auth.transport import requests as ga_requests
        from google.oauth2 import id_token

        claims = id_token.verify_oauth2_token(
            token.strip(), ga_requests.Request(), audience or None
        )
    except AuthError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("[auth] Scheduler token verification failed: %s", exc)
        raise AuthError(401, "Invalid or expired scheduler token") from exc

    email = normalize_email(str(claims.get("email", "")))
    if not email:
        raise AuthError(401, "Scheduler token carries no email claim")
    if not claims.get("email_verified", False):
        raise AuthError(401, "Scheduler token email is not verified")
    if email not in allowed:
        logger.warning("[auth] Rejected /tasks/* call from %s", email)
        raise AuthError(403, "Not an authorised scheduler principal")

    return email


def authenticate(authorization_header: Optional[str]) -> str:
    """
    Full gate: parse the header, verify the token, check the allowlist.

    Returns the operator's email.

    Raises:
        AuthError: 401 for an unusable token, 403 for a valid non-operator.
        The distinction matters — 401 tells the client to sign in again, 403
        tells it that signing in again will not help.
    """
    if not authorization_header:
        raise AuthError(401, "Missing Authorization header")

    scheme, _, token = authorization_header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise AuthError(401, "Authorization header must be 'Bearer <token>'")

    return authorize(verify_token(token.strip()))
