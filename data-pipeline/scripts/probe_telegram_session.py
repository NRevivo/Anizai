r"""
Diagnostic — create (or reuse) a FRESH Telegram session and test whether the
7 registry channels resolve, and whether this account is a MEMBER of each.

WHY THIS EXISTS (2026-08-15 cloud bring-up)
-------------------------------------------
The cloud `telegram` producer authenticates fine but has produced ZERO Bronze
records. Evidence gathered from the running pod:

  - MTProto link ESTABLISHED to a Telegram DC; session DB records dc_id=4.
  - `update_state` pts counters ADVANCE for ~3 channel ids that are NOT in the
    registry, so Telegram is actively pushing updates to the client.
  - `ClashReport` published twice inside the observation window yet has no
    entity row and no pts row in the session cache.
  - Registry channels `abualiexpress`, `Faytuks_Network` and `ClashReport` were
    never resolved by username in the cloud session's entity cache.

Two hypotheses remain, and they need different fixes:

  (A) The cloud session's entity cache is degraded -- it can still receive
      pushes from channels it already knows, but can no longer resolve the
      registry usernames. Fix: regenerate the session.
  (B) The account is not a MEMBER of those channels. Telegram pushes
      `updateNewChannelMessage` only for joined channels; a public channel
      resolves by username without membership and then delivers nothing,
      forever. Fix: join the channels. No rebuild, no new session.

A brand-new session has an EMPTY entity cache, so running the resolution test
against one separates the two cleanly:

  * all 7 resolve + all 7 MEMBER  -> the old session's cache was the problem (A)
  * resolves but NOT A MEMBER     -> membership is the problem (B)
  * fails to resolve on a fresh session -> neither; the defect is in the code
    or the username registry itself

The membership probe is the decisive half and is why this script does more than
log in: `GetParticipantRequest(channel, "me")` raises `UserNotParticipantError`
for a non-member, which is the only direct test of (B).

ASYNC, DELIBERATELY
-------------------
Telethon's `TelegramClient` methods are coroutines. They only appear to work
synchronously if `telethon.sync` is imported, which monkeypatches them; without
it, `client.get_me()` returns a coroutine object and attribute access on it
fails with `AttributeError: 'coroutine' object has no attribute ...`. The one
exception is `start()`, which internally checks whether a loop is running and
blocks if not -- which is why an earlier revision of this script logged in
successfully and then crashed on the very next line.

Rather than rely on that monkeypatching, this script is explicitly async and
awaits every Telethon call: `connect`, `is_user_authorized`, `start`, `get_me`,
`get_entity`, `client(GetParticipantRequest(...))` and `disconnect`. The client
is constructed INSIDE the coroutine so it binds to the loop `asyncio.run`
created, avoiding a loop-mismatch at init.

SAFETY
------
  - Refuses to run if the target path collides with `anizai_cloud.session` (the
    artifact in Secret Manager, mounted into the running pod) or
    `anizai_telegram.session`.
  - Does NOT touch the running cloud producer or its session.
  - Read-only against Telegram: resolves entities and reads own membership.
    Joins nothing, posts nothing, deletes nothing.
  - Re-runnable: if the probe session is already authorised it skips the
    phone/code prompts entirely and goes straight to resolution.

The channel registry is IMPORTED from `ingestion.telegram_producer`, never
copied, so this probe can never drift from the list the producer actually
subscribes to (CLAUDE.md 3.2).

Usage (from data-pipeline/):
    .\venv\Scripts\python.exe scripts\probe_telegram_session.py

Docs: docs/guides/bringup_profiles.md 5 trap 1;
      infrastructure/k8s/producers/telegram-deployment.yaml (session rotation)
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

# ----------------------------------------------------------------------
# sys.path bootstrap -- MUST run before the `ingestion` import below.
#
# Everything else in this project is invoked as `python -m ingestion.x` from
# data-pipeline/, so data-pipeline/ lands on sys.path automatically and the
# problem never surfaces. A script under scripts/ run by PATH does NOT get
# that, and fails with `ModuleNotFoundError: No module named 'ingestion'`.
# Injecting the project root here means the script works from any working
# directory with no PYTHONPATH set by hand.
# ----------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent  # data-pipeline/
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from telethon import TelegramClient  # noqa: E402
from telethon.errors import (  # noqa: E402
    ChannelPrivateError,
    FloodWaitError,
    UsernameInvalidError,
    UsernameNotOccupiedError,
    UserNotParticipantError,
)
from telethon.tl.functions.channels import GetParticipantRequest  # noqa: E402
from telethon.utils import get_peer_id  # noqa: E402

from ingestion.telegram_producer import CHANNELS  # noqa: E402

GCP_PROJECT = "anizai-pipehub"

# Session files that must never be overwritten by this probe.
# anizai_cloud.session is the 28,672-byte artifact currently in Secret Manager
# and CSI-mounted into the running pod.
PROTECTED = {"anizai_cloud.session", "anizai_telegram.session"}

# Telethon appends ".session" to this stem.
SESSION_STEM = "anizai_probe_20260815"


def _secret(name: str) -> str:
    """
    Read a secret from Secret Manager via gcloud.

    Env var wins if set, so the script still works on a machine with a
    populated .env. Falls back to gcloud because this machine has no
    data-pipeline/.env -- the migration moved these values to Secret Manager.
    """
    env = os.environ.get(name)
    if env:
        print(f"  {name}: taken from environment")
        return env.strip()
    try:
        out = subprocess.run(
            [
                "gcloud", "secrets", "versions", "access", "latest",
                f"--secret={name}", f"--project={GCP_PROJECT}",
            ],
            capture_output=True, text=True, check=True, shell=False,
        )
    except FileNotFoundError:
        sys.exit("ERROR: `gcloud` not found on PATH. Open a shell where "
                 "`gcloud --version` works, then re-run.")
    except subprocess.CalledProcessError as exc:
        sys.exit(f"ERROR: could not read secret {name} from {GCP_PROJECT}.\n"
                 f"{exc.stderr.strip()}")
    print(f"  {name}: read from Secret Manager ({GCP_PROJECT})")
    return out.stdout.strip()


async def _resolve_one(client: TelegramClient, username: str) -> tuple[bool, bool, str]:
    """
    Resolve one registry username and probe this account's membership.

    Returns (resolved, is_member, printable_line). Never raises -- a diagnostic
    that aborts on the first bad channel tells you less than one that reports
    all seven.
    """
    try:
        entity = await client.get_entity(username)
    except (UsernameNotOccupiedError, UsernameInvalidError) as exc:
        return False, False, (f"  {username:<18} {'FAILED':<10} {'-':<18} "
                              f"{'-':<16} {type(exc).__name__}")
    except ChannelPrivateError:
        return False, False, (f"  {username:<18} {'FAILED':<10} {'-':<18} "
                              f"{'PRIVATE/BANNED':<16} ChannelPrivateError")
    except FloodWaitError as exc:
        return False, False, (f"  {username:<18} {'FAILED':<10} {'-':<18} "
                              f"{'-':<16} FloodWait {exc.seconds}s -- rerun later")
    except ValueError as exc:
        # Telethon raises plain ValueError for "No user has <x> as username".
        return False, False, (f"  {username:<18} {'FAILED':<10} {'-':<18} "
                              f"{'-':<16} ValueError: {exc}")
    except Exception as exc:  # noqa: BLE001 - diagnostic must not abort
        return False, False, (f"  {username:<18} {'FAILED':<10} {'-':<18} "
                              f"{'-':<16} {type(exc).__name__}: {exc}")

    peer_id = get_peer_id(entity)
    title = getattr(entity, "title", "") or ""

    is_member = False
    try:
        await client(GetParticipantRequest(entity, "me"))
        status = "MEMBER"
        is_member = True
    except UserNotParticipantError:
        status = "NOT A MEMBER"
    except Exception as exc:  # noqa: BLE001
        status = f"? {type(exc).__name__}"

    return True, is_member, (f"  {username:<18} {'OK':<10} {peer_id:<18} "
                             f"{status:<16} {title}")


async def main() -> int:
    session_path = _PROJECT_ROOT / SESSION_STEM
    session_file = session_path.with_suffix(".session")

    if session_file.name in PROTECTED:
        sys.exit("ERROR: refusing to write to a protected session filename.")

    print("=" * 78)
    print("TELEGRAM SESSION PROBE")
    print("=" * 78)
    print("Step 1/3 - reading Telegram API credentials")
    api_id = _secret("TELEGRAM_API_ID")
    api_hash = _secret("TELEGRAM_API_HASH")

    # Constructed inside the coroutine so the client binds to the loop that
    # asyncio.run() created.
    client = TelegramClient(str(session_path), int(api_id), api_hash)

    print(f"\nStep 2/3 - session: {session_file.name}")
    await client.connect()

    if await client.is_user_authorized():
        print("  Existing authorised session found - SKIPPING login "
              "(no phone or code needed).")
    else:
        if not sys.stdin.isatty():
            await client.disconnect()
            sys.exit(
                "ERROR: this session is not authorised and stdin is not a "
                "terminal, so the phone/code prompts cannot be answered.\n"
                "Run this script directly in a PowerShell window."
            )
        print("  No authorised session - logging in.")
        print("  You will be prompted for:")
        print("    * your phone number, international format, e.g. +972501234567")
        print("    * the login code Telegram sends to your Telegram app")
        print("    * your 2FA password, ONLY if you have one set")
        print()
        await client.start()

    me = await client.get_me()
    print(f"  Signed in as: {me.first_name or ''} "
          f"(@{me.username or 'no-username'}, id={me.id})")

    print(f"\nStep 3/3 - resolving {len(CHANNELS)} registry channels\n")
    header = (f"  {'CHANNEL':<18} {'RESOLVE':<10} {'PEER ID':<18} "
              f"{'MEMBERSHIP':<16} TITLE")
    print(header)
    print("  " + "-" * (len(header) - 2))

    resolved = 0
    members = 0
    failures: list[str] = []

    for ch in CHANNELS:
        username = ch["username"]
        ok, is_member, line = await _resolve_one(client, username)
        print(line)
        if ok:
            resolved += 1
            if is_member:
                members += 1
        else:
            failures.append(line.strip())

    total = len(CHANNELS)
    print()
    print("=" * 78)
    print(f"VERDICT: {resolved}/{total} resolved, {members}/{total} joined")
    if resolved == total and members == total:
        print("  -> Fresh session resolves AND is a member of every registry")
        print("     channel. The old cloud session was the problem (A).")
        print("     Rotating the session should fix ingestion.")
    elif resolved == total:
        print("  -> All resolve, but this account is NOT a member of some")
        print("     channels (B). Telegram will never push their messages.")
        print("     Fix is to JOIN the ones marked NOT A MEMBER -- rotating")
        print("     the session alone will NOT fix those.")
    else:
        print("  -> Some channels do not resolve even on this session, so this")
        print("     is not a session-cache problem. Investigate the username")
        print("     registry / producer code.")
    for f in failures:
        print(f"     failure: {f}")
    print("=" * 78)

    print(f"\nSession file: {session_file}")
    if session_file.exists():
        print(f"Size: {session_file.stat().st_size} bytes")
    print("\nNothing was uploaded and the running cloud producer was not "
          "touched. Report the table above before any rollout.")

    await client.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
