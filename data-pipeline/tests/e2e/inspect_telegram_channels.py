"""
Telegram Channel Sampler — quick raw message inspection.

Connects via Telethon and prints 2 recent messages from each of the
7 source-registry channels (Section A.1). No Silver/Gold processing,
no DB writes, no OpenAI calls — raw payload only.

Useful for confirming channel accessibility, checking message structure,
and spotting non-English content before running the full E2E.

Usage (from data-pipeline/ with venv active):
    python -m tests.e2e.inspect_telegram_channels

PowerShell:
    cd C:\\Users\\ronki\\Desktop\\Anizai\\data-pipeline; `
    $env:PYTHONPATH = "."; `
    python -m tests.e2e.inspect_telegram_channels
"""

from __future__ import annotations

import asyncio
import sys

from config.settings import TELEGRAM_API_HASH, TELEGRAM_API_ID
from ingestion.telegram_producer import CHANNELS, SESSION_PATH, TelegramProducer

MESSAGES_PER_CHANNEL = 2


async def _run() -> None:
    if not TELEGRAM_API_ID or not TELEGRAM_API_HASH:
        print("ERROR: TELEGRAM_API_ID / TELEGRAM_API_HASH not set in .env")
        sys.exit(1)

    from telethon import TelegramClient

    client = TelegramClient(SESSION_PATH, int(TELEGRAM_API_ID), TELEGRAM_API_HASH)
    await client.start()
    me = await client.get_me()
    print(f"\nAuthenticated as: {me.username or me.first_name}  (id={me.id})\n")

    total = 0
    for channel_info in CHANNELS:
        username = channel_info["username"]
        title    = channel_info["title"]

        print(f"{'═' * 66}")
        print(f"  @{username}  —  {title}")
        print(f"{'═' * 66}")

        try:
            count = 0
            async for msg in client.iter_messages(username, limit=MESSAGES_PER_CHANNEL):
                # Skip media-only (same filter as producer)
                if not msg.text or not msg.text.strip():
                    print(f"  [msg_id={msg.id}]  ⚠ media-only — skipped")
                    continue

                raw = TelegramProducer._build_raw_payload(msg, username)
                count += 1
                total += 1

                print(f"\n  ── Message {count} ──────────────────────────────────────")
                print(f"  message_id      : {raw['message_id']}")
                print(f"  message_date    : {raw['message_date']}")
                print(f"  views           : {raw['views']}")
                print(f"  is_forwarded    : {raw['is_forwarded']}")
                print(f"  forwarded_from  : {raw['forwarded_from']}")
                print(f"  has_media       : {raw['has_media']}")
                print(f"  extracted_links : {raw['extracted_links']}")
                print(f"  message_url     : {raw['message_url']}")
                print()

                # Word-wrap the message text at 62 chars
                text = raw["message_text"]
                words = text.split()
                line, col = [], 0
                lines = []
                for word in words:
                    need = len(word) + (1 if line else 0)
                    if col + need > 62:
                        lines.append(" ".join(line))
                        line, col = [word], len(word)
                    else:
                        line.append(word)
                        col += need
                if line:
                    lines.append(" ".join(line))

                print(f"  message_text:")
                for l in lines:
                    print(f"    {l}")

        except Exception as exc:
            print(f"  ERROR fetching @{username}: {exc}")

        print()

    await client.disconnect()
    print(f"{'═' * 66}")
    print(f"  Done — {total} messages displayed across {len(CHANNELS)} channels")
    print(f"{'═' * 66}\n")


if __name__ == "__main__":
    asyncio.run(_run())
