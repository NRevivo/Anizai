"""
Sprint 20 T20.11 pre-flight — verify ADC + Firestore reachability.

Read-only check: initializes the Admin SDK via ADC (no service-account
JSON), then reads a non-existent document at `_preflight/_check`. Writes
nothing.

Error-type distinction (per T20.11 plan, user guidance):
  - snapshot.exists is False           → SUCCESS (auth + project OK)
  - DefaultCredentialsError/RefreshError → ADC didn't propagate
                                          (run `gcloud auth application-default login`)
  - PermissionDenied                    → project-level access misconfigured
  - NotFound                            → Firestore database / project ID wrong
  - any other exception                 → unexpected; surface raw type+message

Run from repo root:
    data-pipeline\\venv\\Scripts\\python.exe -m scripts.preflight_firestore
"""

import sys


def main() -> int:
    try:
        from google.api_core import exceptions as gcp_exceptions
        from google.auth import exceptions as auth_exceptions

        from agent.config import settings
        from agent.firestore_client import get_db, init_app
    except ImportError as e:
        print(f"[FAIL] IMPORT_ERROR: {e}")
        return 2

    project_id = settings.FIREBASE_PROJECT_ID
    cred_path = settings.GOOGLE_APPLICATION_CREDENTIALS
    print(f"[INFO] FIREBASE_PROJECT_ID = {project_id!r}")
    print(
        f"[INFO] GOOGLE_APPLICATION_CREDENTIALS = "
        f"{cred_path!r} ({'ADC path active' if not cred_path else 'JSON-file path active'})"
    )

    # Stage 1 — init_app(). Auth errors usually surface here, not at read time.
    try:
        init_app()
    except auth_exceptions.DefaultCredentialsError as e:
        print(
            "[FAIL] AUTH_ERROR (DefaultCredentialsError): ADC not found. "
            "Run `gcloud auth application-default login`."
        )
        print(f"       detail: {e}")
        return 3
    except auth_exceptions.RefreshError as e:
        print(
            "[FAIL] AUTH_ERROR (RefreshError): ADC token refresh failed. "
            "Re-run `gcloud auth application-default login`."
        )
        print(f"       detail: {e}")
        return 3
    except FileNotFoundError as e:
        print(f"[FAIL] CRED_FILE_MISSING: {e}")
        return 3
    except ValueError as e:
        print(f"[FAIL] CONFIG_ERROR: {e}")
        return 3
    except Exception as e:
        print(f"[FAIL] init_app() raised unexpected: {type(e).__name__}: {e}")
        return 4

    # Stage 2 — read a non-existent doc. Project-access errors surface here.
    try:
        db = get_db()
        ref = db.collection("_preflight").document("_check")
        snapshot = ref.get()
        exists = snapshot.exists
    except gcp_exceptions.PermissionDenied as e:
        print(
            "[FAIL] PERMISSION_DENIED: project-level access misconfigured. "
            f"Account does not have read access to Firestore in {project_id!r}."
        )
        print(f"       detail: {e}")
        return 5
    except gcp_exceptions.NotFound as e:
        print(
            "[FAIL] PROJECT_OR_DB_NOT_FOUND: Firestore database not found. "
            f"Verify project_id ({project_id!r}) and that Firestore is enabled."
        )
        print(f"       detail: {e}")
        return 6
    except auth_exceptions.RefreshError as e:
        print(
            "[FAIL] AUTH_ERROR (RefreshError during read): token expired / "
            "refresh blocked. Re-run `gcloud auth application-default login`."
        )
        print(f"       detail: {e}")
        return 3
    except Exception as e:
        print(f"[FAIL] Unexpected during read: {type(e).__name__}: {e}")
        return 7

    if exists:
        # Surprising — but auth still OK. Surface and continue green.
        print(
            "[WARN] _preflight/_check exists (unexpected — was the script "
            "run before with a write?). Auth + project access verified OK."
        )
    else:
        print(
            f"[OK] ADC + Firestore reachable for project={project_id} "
            "(read returned exists=False as expected)"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
