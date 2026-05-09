#!/usr/bin/env python3
"""
Phase 7A pre-migration shape validation for newsapi.ai (Event Registry).

One-time smoke test — NOT part of the production pipeline.
Makes 6 HTTPS GET requests (one per category URI) and validates the response
fields that _fetch_articles / _build_raw_payload / _passes_whitelist will rely
on post-migration.

Usage:
    python scripts/validate_newsai.py --api-key <NEWSAI_API_KEY>

Budget: 6 calls = 6 tokens (Phase 7A cap: 50).
Idempotent — safe to re-run at any time before T7A.3.
Deleted in T7A.16 after E2E passes (M8, M9).

No Kafka, no DB, no imports from ingestion/ or processing/.

References:
    - M1-M9 (Settled Design Decisions, Phase 7A)
    - T7A.1: build this script
    - T7A.2: Ron runs it live and captures output
    - Section B.4: Authority Whitelist, category URI mapping
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

import requests

ENDPOINT = "https://eventregistry.org/api/v1/article/getArticles"

# The 6 category URIs the production producer will use (M4).
# Order matches PULSE_CATEGORIES + GENERAL_CATEGORY in the migrated producer.
CATEGORIES: list[tuple[str, str]] = [
    ("news/Business",   "Business"),
    ("news/Technology", "Technology"),
    ("news/Health",     "Health"),
    ("news/Science",    "Science"),
    ("news/Politics",   "Politics (NEW — was not available on TheNewsAPI)"),
    ("news",            "General / root (GENERAL_KEYWORDS pre-filter applies here)"),
]

# M8: body < this threshold → known-gap WARNING, not a hard failure.
# Body absent entirely → hard ERROR (content field would be empty post-migration).
BODY_MIN_CHARS = 200


def _check_category(api_key: str, uri: str, label: str) -> dict:
    """
    Fetch 1 article for a category URI and assert all fields T7A.5/T7A.6 consume.

    Uses articlesCount=1 to minimise token spend — shape validation requires
    only one article. Returns a result dict the caller uses for display + summary.
    """
    params: dict = {
        "apiKey":             api_key,
        "categoryUri":        uri,
        "resultType":         "articles",
        "articlesSortBy":     "date",
        "articleBodyLen":     -1,       # -1 = full body; free tier may cap silently (M9)
        "includeArticleBody": "true",   # lowercase string — URL param, not JSON
        "lang":               "eng",    # ISO 639-3 replaces language=en (M5)
        "articlesPage":       1,
        "articlesCount":      1,        # 1 article sufficient for shape validation
    }

    resp = requests.get(ENDPOINT, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    # --- Envelope path check (critical for T7A.5 hardcode) ---
    articles_block = data.get("articles", {})
    results        = articles_block.get("results", [])
    total          = articles_block.get("totalResults", 0)

    out: dict = {
        "uri":           uri,
        "label":         label,
        "total_results": total,
        "article_count": len(results),
        "ok":            True,
        "warnings":      [],
        "errors":        [],
    }

    if not results:
        out["errors"].append(
            "No articles returned — cannot validate field shape. "
            "The category URI may not be accepted by the API."
        )
        out["ok"] = False
        return out

    art = results[0]
    out["title"] = (art.get("title") or "")[:100]

    # --- body -> content (M7) ---
    body = art.get("body") or ""
    out["body_length"]  = len(body)
    out["body_preview"] = body[:120].replace("\n", " ") if body else ""
    if not body:
        out["errors"].append(
            "body field is absent or empty. "
            "_build_raw_payload's content field would be ''. "
            "Migration cannot proceed."
        )
        out["ok"] = False
    elif len(body) < BODY_MIN_CHARS:
        out["warnings"].append(
            f"body is {len(body)} chars (< {BODY_MIN_CHARS} threshold). "
            "Likely free-tier cap despite articleBodyLen=-1. "
            "Per M9: document as known gap and proceed — full body arrives post upgrade."
        )

    # --- source.uri -> source.id (M7, M3) ---
    source    = art.get("source") or {}
    uri_val   = source.get("uri") or ""
    title_val = source.get("title") or ""
    out["source_uri"]   = uri_val
    out["source_title"] = title_val
    if not uri_val:
        out["errors"].append(
            "source.uri is absent. "
            "_passes_whitelist reads article['source']['uri'] — all articles would be rejected."
        )
        out["ok"] = False

    # --- authors[] -> joined author string (M7) ---
    authors = art.get("authors")
    if authors is None:
        out["errors"].append(
            "authors key missing. _build_raw_payload does: ', '.join(article.get('authors', []) or [])"
        )
        out["ok"] = False
    elif not isinstance(authors, list):
        out["errors"].append(
            f"authors field is {type(authors).__name__}, expected list. "
            "join() would raise TypeError."
        )
        out["ok"] = False
    else:
        out["authors"] = authors

    # --- dateTime -> published_at (M7) ---
    dt_str = art.get("dateTime") or ""
    out["dateTime"] = dt_str
    if not dt_str:
        out["errors"].append("dateTime is absent or empty. published_at would be ''.")
        out["ok"] = False
    else:
        try:
            datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        except ValueError:
            out["errors"].append(
                f"dateTime {dt_str!r} is not ISO8601-parseable. "
                "Downstream published_at parsing in Silver would fail."
            )
            out["ok"] = False

    # --- image -> url_to_image (M7, informational) ---
    out["image_present"] = bool(art.get("image"))

    # --- url -> article_id + url (M7, informational) ---
    out["url_present"] = bool(art.get("url"))

    return out


def _print_result(r: dict) -> None:
    label = r["label"]
    uri   = r["uri"]
    width = 14
    print(f"\n  [{label}]")
    print(f"    {'categoryUri':<{width}}: {uri!r}")
    print(f"    {'total_results':<{width}}: {r.get('total_results', 'N/A')}")
    print(f"    {'article_count':<{width}}: {r.get('article_count', 'N/A')}")
    if r.get("title"):
        print(f"    {'title':<{width}}: {r['title']!r}")
    if r.get("source_uri"):
        print(f"    {'source.uri':<{width}}: {r['source_uri']!r}")
    if r.get("source_title"):
        print(f"    {'source.title':<{width}}: {r['source_title']!r}")
    if r.get("authors") is not None:
        print(f"    {'authors':<{width}}: {r['authors']}")
    if r.get("dateTime"):
        print(f"    {'dateTime':<{width}}: {r['dateTime']!r}")
    if "body_length" in r:
        print(f"    {'body_length':<{width}}: {r['body_length']} chars")
    if r.get("body_preview"):
        print(f"    {'body_preview':<{width}}: {r['body_preview']!r}")
    print(f"    {'image':<{width}}: {'present' if r.get('image_present') else 'absent'}")
    print(f"    {'url':<{width}}: {'present' if r.get('url_present') else 'absent'}")
    for w in r.get("warnings", []):
        print(f"    WARN  : {w}")
    for e in r.get("errors", []):
        print(f"    ERROR : {e}")
    print(f"    STATUS: {'PASS' if r.get('ok') else 'FAIL'}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Phase 7A pre-migration validation for newsapi.ai (Event Registry). "
            "Run before T7A.3 to confirm API shape and response envelope."
        )
    )
    parser.add_argument(
        "--api-key",
        required=True,
        metavar="KEY",
        help="NEWSAI_API_KEY from eventregistry.org account.",
    )
    args = parser.parse_args()

    sep = "=" * 70
    print(sep)
    print("newsapi.ai (Event Registry) — Phase 7A Shape Validation  [T7A.1]")
    print(f"  Endpoint : {ENDPOINT}")
    print(f"  Calls    : {len(CATEGORIES)} (one per category URI)")
    print(f"  Tokens   : {len(CATEGORIES)} of 50 Phase-7A budget")
    print(sep)

    all_results: list[dict] = []
    envelope_printed = False

    for uri, label in CATEGORIES:
        try:
            r = _check_category(args.api_key, uri, label)
            all_results.append(r)

            # Print envelope confirmation once (for T7A.2 output capture)
            if not envelope_printed and r.get("article_count", 0) > 0:
                envelope_printed = True
                print(
                    "\n  ENVELOPE PATH CONFIRMED: "
                    "data['articles']['results'] / data['articles']['totalResults']"
                    "\n  -> T7A.5 will hardcode this path in _fetch_articles()."
                )

            _print_result(r)

        except requests.HTTPError as exc:
            err = {"uri": uri, "label": label, "ok": False, "errors": [str(exc)], "warnings": []}
            all_results.append(err)
            print(f"\n  [{label}]\n    ERROR (HTTP): {exc}")
        except requests.RequestException as exc:
            err = {"uri": uri, "label": label, "ok": False, "errors": [str(exc)], "warnings": []}
            all_results.append(err)
            print(f"\n  [{label}]\n    ERROR (network): {exc}")

    # --- Summary ---
    passed  = sum(1 for r in all_results if r.get("ok"))
    warned  = sum(len(r.get("warnings", [])) for r in all_results)
    failed  = len(all_results) - passed

    print(f"\n{sep}")
    print(f"SUMMARY  |  {len(CATEGORIES)} categories tested")
    print(f"  Passed   : {passed} / {len(CATEGORIES)}")
    if warned:
        print(f"  Warnings : {warned}  (known-gap per M9 — non-blocking)")
    if failed:
        print(f"  Failed   : {failed}")
        print("  RESULT   : FAIL — resolve errors above before proceeding to T7A.3.")
    else:
        print("  RESULT   : PASS — share output with Ron (T7A.2), then proceed to T7A.3.")
    print(sep)

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
