# A NewsAPI Article — End-to-End Walkthrough

This document traces a single Reuters article about an OPEC production cut through every layer of the pipeline: **Ingestion → Bronze → Silver → Gold → Persistence**. Every code block is taken directly from the source files.

---

## The Journey at a Glance

```
NewsAPI /top-headlines?category=business
    ↓  _passes_whitelist()       Reuters ✓
    ↓  _passes_keyword_sniper()  "opec" found ✓ (general category only)
    ↓  _build_raw_payload()      article dict + impact_boost flag
    ↓  build_bronze_message()    envelope { event_id, payload { raw_payload } }
    ↓  producer.send(BRONZE_NEWSAPI, key="reuters")
       [Kafka: ingest.bronze.newsapi]
    ↓  process_newsapi_message() validate → snipe → hash → map to Silver
    ↓  producer.send(SILVER_GLOBAL_NEWS, silver_record)
       [Kafka: process.silver.global_news]
    ↓  process_newsapi_gold_message() is_high_signal guard → GPT-4o → embedding
    ↓  knowledge_vault.archive()      INSERT full-text record
    ↓  knowledge_vectors.insert()     INSERT Gold record + 1536-dim vector
```

---

## Step 1 — The Producer

### 1a. The main polling loop — `run_pulse()`

**File:** `ingestion/newsapi_producer.py` lines 534–586

```python
def run_pulse(self) -> int:
    self._emitted = self._filtered_whitelist = self._filtered_keyword = 0

    all_categories = PULSE_CATEGORIES + [GENERAL_CATEGORY]
    for category in all_categories:
        try:
            articles, _, duration_ms = self._fetch_top_headlines(category)
            emitted = self._process_and_emit(articles, category, "pulse", duration_ms)
        except requests.HTTPError as exc:
            logger.error("[newsapi] HTTP error for category=%s: %s — skipping", category, exc)
        finally:
            time.sleep(REQUEST_DELAY_SEC)

    self._producer.flush()
    return self._emitted
```

**What it does:** Loops over 5 categories (`business`, `technology`, `health`, `science`, `general`). For each one, fetches up to 100 headlines and passes them to `_process_and_emit`. At the end, `flush()` forces Kafka to drain any buffered messages before the process exits.

---

> **Python syntax — Tuple unpacking**
>
> ```python
> articles, _, duration_ms = self._fetch_top_headlines(category)
> ```
>
> `_fetch_top_headlines` returns three values as a **tuple**: `(articles, total_results, duration_ms)`.  
> The `articles, _, duration_ms =` syntax unpacks all three at once. The `_` is a Python convention meaning "I don't care about this value."
>
> Short standalone example:
> ```python
> def get_info():
>     return "Alice", 30, "engineer"
>
> name, _, job = get_info()   # ignores the age
> print(name)  # → "Alice"
> print(job)   # → "engineer"
> ```

---

> **Python syntax — `try / except / finally`**
>
> ```python
> try:
>     articles, _, duration_ms = self._fetch_top_headlines(category)
> except requests.HTTPError as exc:
>     logger.error("... %s", exc)
> finally:
>     time.sleep(REQUEST_DELAY_SEC)
> ```
>
> - `try` — run this block.  
> - `except SomeError as exc` — if that specific error is raised, run this instead and bind the error object to `exc` so you can log or inspect it.  
> - `finally` — always run this, whether or not an error occurred. Here it ensures the 1-second delay happens between every category even if a request fails.
>
> Short standalone example:
> ```python
> try:
>     result = 10 / 0
> except ZeroDivisionError as e:
>     print(f"Error: {e}")    # → Error: division by zero
> finally:
>     print("always runs")    # → always runs
> ```

---

### 1b. The fetch — `_fetch_top_headlines()`

**File:** `ingestion/newsapi_producer.py` lines 289–332

```python
def _fetch_top_headlines(self, category: str, page: int = 1) -> tuple[list[dict], int, int]:
    params: dict = {
        "category": category,
        "pageSize": PAGE_SIZE,    # 100
        "page":     page,
        "apiKey":   NEWS_API_KEY,
    }
    response, duration_ms = timed_request(
        lambda: requests.get(TOP_HEADLINES_ENDPOINT, params=params, timeout=20)
    )
    response.raise_for_status()

    data          = response.json()
    articles      = data.get("articles") or []
    total_results = data.get("totalResults") or 0
    return articles, total_results, duration_ms
```

**What it does:** Calls `https://newsapi.org/v2/top-headlines?category=business&pageSize=100&...`, returns the list of article dicts, the total count, and how long the HTTP request took.

---

> **Python syntax — `lambda`**
>
> ```python
> timed_request(lambda: requests.get(TOP_HEADLINES_ENDPOINT, params=params, timeout=20))
> ```
>
> A `lambda` is an anonymous (nameless) function written inline. `lambda: X` means "a function that takes no arguments and returns X when called."
>
> `timed_request` needs a *callable* so it can measure how long the call takes. Instead of defining a named function just for this, we wrap `requests.get` in a lambda.
>
> Short standalone example:
> ```python
> # Named version:
> def say_hi():
>     return "hi"
>
> # Lambda version — identical behaviour:
> say_hi = lambda: "hi"
>
> print(say_hi())   # → "hi"
> ```

---

> **Python syntax — Type hints**
>
> ```python
> def _fetch_top_headlines(self, category: str, page: int = 1) -> tuple[list[dict], int, int]:
> ```
>
> The `->` annotation is a return type hint. It tells the reader (and tools like mypy) what this function returns.  
> `tuple[list[dict], int, int]` means: a tuple containing three items — a list of dicts, an int, and another int.  
> Python does **not** enforce these at runtime; they are documentation only.

---

### 1c. The filter loop — `_process_and_emit()`

**File:** `ingestion/newsapi_producer.py` lines 495–528

```python
def _process_and_emit(self, articles, category, fetch_mode, duration_ms) -> int:
    emitted = 0
    for article in articles:
        # Gate 1 — Authority whitelist (always applied)
        if not self._passes_whitelist(article):
            self._filtered_whitelist += 1
            continue

        # Gate 2 — Keyword Sniper on General category only
        if category == GENERAL_CATEGORY and not self._passes_keyword_sniper(article):
            self._filtered_keyword += 1
            continue

        raw_payload = self._build_raw_payload(article, category, fetch_mode)
        self._emit(raw_payload, duration_ms)
        emitted += 1

    return emitted
```

**What it does:** Two gates. Gate 1 checks every article — is the source (Reuters, BBC, etc.) on the whitelist? Gate 2 is only for the `general` category — does the title or description mention at least one of 9 keywords like `"nato"`, `"crude oil"`, `"sanctions"`? Anything that passes both gates is forwarded to `_emit`.

---

**Gate 1 — The whitelist check** (`_passes_whitelist`):

**File:** `ingestion/newsapi_producer.py` lines 238–249

```python
def _passes_whitelist(self, article: dict) -> bool:
    source      = article.get("source") or {}
    source_id   = (source.get("id")   or "").strip().lower()
    source_name = (source.get("name") or "").strip().lower()
    return source_id in AUTHORITY_WHITELIST or source_name in AUTHORITY_WHITELIST
```

`AUTHORITY_WHITELIST` is a `frozenset`. A `frozenset` is like a regular Python `set` — checking membership with `in` is O(1) (instant, no loop needed) — but it's **frozen**, meaning it cannot be modified after creation. The `or ""` pattern is a safe fallback: if `get("id")` returns `None`, use `""` instead.

---

**Gate 2 — The keyword pre-check** (`_passes_keyword_sniper`):

**File:** `ingestion/newsapi_producer.py` lines 251–266

```python
def _passes_keyword_sniper(self, article: dict) -> bool:
    text = " ".join(filter(None, [
        article.get("title")       or "",
        article.get("description") or "",
    ])).lower()
    return any(kw in text for kw in GENERAL_KEYWORDS)
```

---

> **Python syntax — `filter(None, [...])`**
>
> `filter(None, iterable)` removes any falsy values (`None`, empty strings, `0`, `False`) from an iterable.  
> Here, if `description` is `None`, it gets dropped before the join so it doesn't become the string `"None"`.
>
> Short standalone example:
> ```python
> list(filter(None, ["hello", "", None, "world"]))
> # → ["hello", "world"]
> ```

---

> **Python syntax — `any()` with a generator expression**
>
> ```python
> any(kw in text for kw in GENERAL_KEYWORDS)
> ```
>
> `any(...)` returns `True` if at least one item in the iterable is truthy.  
> `kw in text for kw in GENERAL_KEYWORDS` is a **generator expression** — it produces `True`/`False` values one at a time and `any()` stops the moment it finds the first `True`. Much more efficient than building a whole list first.
>
> Short standalone example:
> ```python
> keywords = {"oil", "nato", "sanctions"}
> text = "NATO expands eastern flank"
> any(kw in text for kw in keywords)   # → True (stops at "nato")
> ```

---

### 1d. Building the raw payload — `_build_raw_payload()`

**File:** `ingestion/newsapi_producer.py` lines 399–453

```python
def _build_raw_payload(self, article, category, fetch_mode) -> dict:
    source        = article.get("source") or {}
    boost, reason = self._impact_boost_info(article)

    return {
        "article_id":    article.get("url") or "",
        "title":         article.get("title") or "",
        "description":   article.get("description") or "",
        "url":           article.get("url") or "",
        "url_to_image":  article.get("urlToImage") or "",
        "published_at":  article.get("publishedAt") or "",
        "content":       article.get("content") or "",
        "author":        article.get("author") or "",
        "source": {
            "id":   source.get("id")   or "",
            "name": source.get("name") or "",
        },
        # Producer-injected fields consumed by Silver / Gold Jobs
        "category":            category,
        "fetch_mode":          fetch_mode,
        "impact_boost":        boost,
        "impact_boost_reason": reason,
    }
```

**What it does:** Copies API response fields verbatim (Bronze = no transformation) and adds three producer-injected fields: `category`, `fetch_mode`, and the `impact_boost` flag pre-computed from a cheap string scan. Note that `article_id` is set to the URL — NewsAPI has no stable numeric ID, so the URL is the canonical deduplication key.

---

### 1e. Emitting to Kafka — `_emit()` and `build_bronze_message()`

**File:** `ingestion/newsapi_producer.py` lines 459–489

```python
def _emit(self, raw_payload: dict, duration_ms: int = 0) -> None:
    source = raw_payload["source"]
    partition_key = source["id"] or source["name"] or "unknown"

    endpoint = (
        f"{TOP_HEADLINES_ENDPOINT}?category={raw_payload['category']}"
        if raw_payload["fetch_mode"] == "pulse"
        else EVERYTHING_ENDPOINT
    )

    msg = build_bronze_message(
        source_name=SOURCE_NAME,
        source_endpoint=endpoint,
        raw_payload=raw_payload,
        http_status_code=200,
        request_duration_ms=duration_ms,
    )
    self._producer.send(BRONZE_NEWSAPI, value=msg, key=partition_key)
    self._emitted += 1
```

The partition key is `source["id"]` (e.g. `"reuters"`). This means all Reuters articles go to the same Kafka partition — which ensures the Silver Job's deduplication logic sees articles from the same publisher on the same consumer thread.

---

#### Inside `build_bronze_message()` — `utils/kafka_utils.py`

This is a convenience wrapper that calls two functions in sequence:

**File:** `utils/kafka_utils.py` lines 150–180

```python
def build_bronze_message(source_name, source_endpoint, raw_payload,
                         http_status_code=200, request_duration_ms=0) -> dict:
    bronze_payload = build_bronze_payload(
        source_name=source_name,
        source_endpoint=source_endpoint,
        raw_payload=raw_payload,
        http_status_code=http_status_code,
        request_duration_ms=request_duration_ms,
    )
    return build_envelope(bronze_payload, source_name)
```

**Step A — `build_bronze_payload()` (the Bronze Schema, Section C.1):**

**File:** `utils/kafka_utils.py` lines 111–147

```python
def build_bronze_payload(...) -> dict:
    return {
        "ingestion_id":        str(uuid.uuid4()),
        "source_name":         source_name,
        "source_endpoint":     source_endpoint,
        "ingestion_timestamp": datetime.now(timezone.utc).isoformat(),
        "producer_version":    PRODUCER_VERSION,
        "raw_payload":         raw_payload,       # ← the article dict, nested here
        "metadata": {
            "http_status_code":    http_status_code,
            "request_duration_ms": request_duration_ms,
        },
    }
```

**Step B — `build_envelope()` (the outer Message Envelope, Section 3.2):**

**File:** `utils/kafka_utils.py` lines 73–104

```python
def build_envelope(payload: dict, source_name: str) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "event_id":           str(uuid.uuid4()),
        "trace_id":           str(uuid.uuid4()),
        "producer_timestamp": now,
        "schema_version":     ENVELOPE_SCHEMA_VERSION,
        "source_name":        source_name,
        "payload":            payload,     # ← the bronze_payload from Step A
    }
```

**The final object Kafka receives is shaped like this:**

```
{
  "event_id":           "abc-123..."   ← becomes canonical_event_id in Silver
  "trace_id":           "def-456..."
  "producer_timestamp": "2026-04-09T10:00:00Z"
  "schema_version":     "1.0"
  "source_name":        "newsapi"
  "payload": {
      "ingestion_id":   "ghi-789..."
      "source_endpoint": "https://newsapi.org/v2/top-headlines?category=business"
      "raw_payload": {
          "title":         "OPEC cuts oil production by 1 million barrels"
          "url":           "https://reuters.com/article/..."
          "impact_boost":  true
          ...
      }
      "metadata": { "http_status_code": 200, "request_duration_ms": 342 }
  }
}
```

This dict is automatically serialized to NDJSON bytes by the Kafka producer's `value_serializer`:

**File:** `utils/kafka_utils.py` lines 48–56

```python
def ndjson_serializer(data: dict) -> bytes:
    return (json.dumps(data, ensure_ascii=False, default=str) + "\n").encode("utf-8")
```

The `+ "\n"` is the NDJSON spec: every record ends with a newline. `.encode("utf-8")` converts the Python string to bytes that Kafka transmits over the wire.

The Kafka producer was created with this serializer wired in at startup:

**File:** `utils/kafka_utils.py` lines 217–225

```python
return KafkaProducer(
    bootstrap_servers=bootstrap_servers,
    value_serializer=ndjson_serializer,                             # runs on every .send()
    key_serializer=lambda k: k.encode("utf-8") if k else None,     # encode partition key
    acks="all",
    retries=5,
    compression_type="gzip",
)
```

---

## Step 2 — Silver Job

### `process_newsapi_message()` — Validate → Map → Route

**File:** `processing/silver_job.py` lines 622–715

```python
def process_newsapi_message(envelope: dict) -> tuple[Optional[str], Optional[dict]]:
    # Gate 1: validate the outer envelope shape
    env_result = validate_envelope(envelope)
    if not env_result.is_valid:
        return DEAD_LETTER_QUEUE, _newsapi_dlq_record(envelope, env_result.errors, "envelope")

    payload = envelope.get("payload", {})

    # Gate 2: validate the Bronze payload shape
    bronze_result = validate_bronze_payload(payload)
    if not bronze_result.is_valid:
        return DEAD_LETTER_QUEUE, _newsapi_dlq_record(envelope, bronze_result.errors, "bronze_payload")

    raw = payload.get("raw_payload", {})

    # Guard: URL must exist — it is the canonical dedup key
    if not (raw.get("url") or "").strip():
        errors = ["raw_payload.url is empty — URL is the dedup key for hash_document() (Section 4.1C)."]
        return DEAD_LETTER_QUEUE, _newsapi_dlq_record(envelope, errors, "url_guard")

    # Map to Silver schema
    silver = map_newsapi_article_to_silver(raw, envelope)

    # Gate 3: validate the assembled Silver document
    result = validate_silver_document(silver)
    if not result.is_valid:
        return DEAD_LETTER_QUEUE, _newsapi_dlq_record(envelope, result.errors, "silver_document")

    return SILVER_GLOBAL_NEWS, silver
```

**The DLQ rule:** Any failure at any gate returns `(DEAD_LETTER_QUEUE, error_record)`. Nothing is silently dropped — that is Section 3.5 of the spec. The article is preserved in the DLQ record with exact error details, ready for replay.

---

> **Python syntax — `Optional[str]` and return type tuples**
>
> ```python
> def process_newsapi_message(envelope: dict) -> tuple[Optional[str], Optional[dict]]:
> ```
>
> This function always returns a **pair**: `(topic_name, record)`.  
> `Optional[str]` is a type hint meaning "this can be either a `str` or `None`."  
> It is shorthand for `str | None`. Python does not enforce it at runtime.
>
> Every code path returns a pair:
> - Success: `(SILVER_GLOBAL_NEWS, silver_dict)`
> - Failure: `(DEAD_LETTER_QUEUE, error_dict)`
> - Gold-layer intentional skip: `(None, None)`
>
> Short standalone example:
> ```python
> from typing import Optional
>
> def find_user(id: int) -> Optional[dict]:
>     # Returns a user dict if found, or None if not found
>     ...
> ```

---

### `map_newsapi_article_to_silver()` — the actual transformation

**File:** `processing/silver_job.py` lines 545–619

```python
def map_newsapi_article_to_silver(raw: dict, envelope: dict) -> dict:
    url         = raw.get("url", "")
    description = raw.get("description", "")
    content     = raw.get("content", "")
    full_text   = content or description   # prefer content; fall back to lede

    sniper   = snipe_article(raw)          # Keyword Sniper (Section 4.1A)
    doc_hash = hash_document(full_text, url)   # SHA-256 dedup hash (Section 4.1C)

    source = raw.get("source") or {}

    return {
        "doc_id":                str(uuid.uuid4()),
        "document_hash":         doc_hash,
        "canonical_event_id":    envelope.get("event_id", ""),
        "full_text_raw":         full_text,
        "inverted_pyramid_lead": description,
        "source_name":           "newsapi",
        "original_url":          url,
        "author":                raw.get("author", ""),
        "publish_date":          raw.get("published_at", ""),
        "detected_entities":     [],          # populated later by Gold Job (OpenAI)
        "relevance_score":       sniper.relevance_score,
        "title":                 raw.get("title", ""),
        "source_display_name":   source.get("name", ""),
        "category":              raw.get("category", ""),
        "impact_boost":          bool(raw.get("impact_boost", False)),
        "impact_boost_reason":   raw.get("impact_boost_reason", ""),
        "sniper_keywords":       sniper.matched_keywords,
        "is_high_signal":        sniper.is_high_signal,
        "fetch_mode":            raw.get("fetch_mode", "pulse"),
        "bronze_ref":            envelope.get("event_id", ""),
    }
```

---

> **Python syntax — `or` as a fallback operator**
>
> ```python
> full_text = content or description
> ```
>
> Python's `or` with strings: if `content` is an empty string (falsy in Python), Python evaluates the right side and returns `description`. This is a very common Python idiom for "use the first non-empty value."
>
> ```python
> "" or "fallback"      # → "fallback"
> "real value" or "fb"  # → "real value"
> None or "fallback"    # → "fallback"
> ```

---

### `snipe_article()` → `snipe()` — the Keyword Sniper

**File:** `processing/keyword_sniper.py` lines 434–461 and 358–427

```python
# Thin wrapper — extracts the three text fields and calls snipe()
def snipe_article(raw_payload: dict, threshold=DEFAULT_THRESHOLD) -> SniperResult:
    return snipe(
        title=raw_payload.get("title", ""),
        description=raw_payload.get("description", ""),
        content=raw_payload.get("content", ""),
        threshold=threshold,
    )


# The actual scoring engine
def snipe(title, description="", content="", threshold=DEFAULT_THRESHOLD) -> SniperResult:
    title_norm       = _normalise(title)
    description_norm = _normalise(description)
    content_norm     = _normalise(content)

    raw_score         = 0.0
    matched_keywords: list[str] = []

    for keyword in MASTER_KEYWORD_LIST:
        if _contains(title_norm, keyword):
            raw_score += TITLE_WEIGHT        # 2.0 — headline is highest signal
            matched_keywords.append(keyword)
        elif _contains(description_norm, keyword):
            raw_score += DESCRIPTION_WEIGHT  # 1.5
            matched_keywords.append(keyword)
        elif _contains(content_norm, keyword):
            raw_score += CONTENT_WEIGHT      # 1.0
            matched_keywords.append(keyword)

    relevance_score = min(1.0, raw_score / SCORE_SATURATION)   # cap at 1.0; saturation = 10.0
    keyword_density = len(matched_keywords) / len(MASTER_KEYWORD_LIST)
    is_high_signal  = relevance_score >= threshold              # default threshold = 0.09

    return SniperResult(
        is_high_signal=is_high_signal,
        relevance_score=round(relevance_score, 4),
        matched_keywords=sorted(matched_keywords),
        keyword_density=round(keyword_density, 4),
    )
```

**Concrete example** for our OPEC headline `"OPEC cuts oil production by 1 million barrels"`:
- `"opec"` found in title → `raw_score += 2.0`
- `"production cut"` found in title → `raw_score += 2.0`
- `"energy"` found in description → `raw_score += 1.5`
- Total: `raw_score = 5.5` → `relevance_score = 5.5 / 10.0 = 0.55`
- `0.55 >= 0.09` → `is_high_signal = True`

Each keyword is counted **at most once**, in the highest-weight field where it appears. Finding `"opec"` in both the title and description doesn't double-count it.

---

> **Python syntax — `@dataclass` and `field(default_factory=list)`**
>
> `SniperResult` is defined with a decorator:
>
> ```python
> @dataclass
> class SniperResult:
>     is_high_signal:   bool
>     relevance_score:  float
>     matched_keywords: list[str] = field(default_factory=list)
>     keyword_density:  float = 0.0
> ```
>
> `@dataclass` is a **decorator** — the `@` symbol means "apply this function to the class below." It automatically generates `__init__`, `__repr__`, and other boilerplate so you don't have to write `self.is_high_signal = is_high_signal` for every field.
>
> `field(default_factory=list)` means "the default value for this field is a **new empty list** created per instance." You can't write `= []` directly as a default in a dataclass because Python would share the same list object across all instances — a classic mutable default bug.
>
> Short standalone example:
> ```python
> from dataclasses import dataclass
>
> @dataclass
> class Point:
>     x: float
>     y: float
>
> p = Point(1.5, 2.0)   # auto-generated __init__, no manual assignment needed
> print(p.x)             # → 1.5
> ```

---

### `hash_document()` — the deduplication key

**File:** `processing/deduplication.py` lines 57–79

```python
def hash_document(full_text: str, url: str = "") -> str:
    normalised = " ".join(full_text.lower().split())   # collapse all whitespace
    return sha256_hash(f"{url.strip()}|{normalised}")


def sha256_hash(content) -> str:
    if isinstance(content, str):
        data = content
    else:
        data = json.dumps(content, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()
```

**What it does:** Takes the article body and URL, normalises whitespace, concatenates them with `|`, then runs SHA-256. The result is a 64-character hex string like `"a3f7b2c1..."`. The Knowledge Vault checks this hash before every insert — if the hash already exists, the article is a duplicate and the insert is skipped.

---

> **Python syntax — f-strings**
>
> ```python
> f"{url.strip()}|{normalised}"
> ```
>
> An f-string has an `f` prefix before the opening quote. Python evaluates any expression inside `{}` and inserts it inline.
>
> Short standalone example:
> ```python
> name  = "Reuters"
> score = 0.87
> f"Source: {name}, score: {score:.2f}"
> # → "Source: Reuters, score: 0.87"
> ```
> The `:2f` inside `{}` is a format spec — it rounds the float to 2 decimal places.

---

## Step 3 — Gold Job

### `process_newsapi_gold_message()` — OpenAI Enrichment

**File:** `processing/gold_job.py` lines 938–1042

```python
def process_newsapi_gold_message(silver_doc, openai_client=None):
    # Guard: skip low-signal articles — zero OpenAI token spend
    if not silver_doc.get("is_high_signal", False):
        return None, None   # intentional skip, not an error

    # Re-validate the Silver document
    silver_result = validate_silver_document(silver_doc)
    if not silver_result.is_valid:
        return DEAD_LETTER_QUEUE, _newsapi_gold_dlq_record(...)

    # Lazy-create OpenAI client if not injected
    if openai_client is None:
        from openai import OpenAI
        from config.settings import OPENAI_API_KEY
        openai_client = OpenAI(api_key=OPENAI_API_KEY)

    # Step 1: GPT-4o Cognitive Metadata Extraction
    try:
        ai_meta = call_openai_cognitive_metadata(silver_doc, openai_client)
    except Exception as exc:
        return DEAD_LETTER_QUEUE, _newsapi_gold_dlq_record(
            silver_doc, [f"OpenAI cognitive metadata error: {exc}"], "openai_cognitive_metadata"
        )

    # Step 2: Apply Impact Boost (+1 to impact_level if Bronze-flagged)
    ai_meta = apply_impact_boost(ai_meta, silver_doc)

    # Step 3: Generate the semantic embedding vector
    try:
        embedding = call_openai_embedding(
            ai_meta.get("executive_summary") or silver_doc.get("title", ""),
            openai_client,
        )
    except Exception as exc:
        return DEAD_LETTER_QUEUE, _newsapi_gold_dlq_record(...)

    # Assemble and validate the Gold record
    gold_record = build_gold_global_signal(silver_doc, ai_meta, embedding)
    gold_result = validate_gold_signal(gold_record)
    if not gold_result.is_valid:
        return DEAD_LETTER_QUEUE, _newsapi_gold_dlq_record(...)

    return GOLD_GLOBAL_NEWS, gold_record
```

The `is_high_signal` guard at the top is the key cost-saving mechanism from Section 4.1A. A low-signal article returns `(None, None)` — no OpenAI API call is made, no token is spent.

---

### The prompt sent to GPT-4o

Two parts are assembled and sent together:

**System prompt** (fixed — defines the role and the exact JSON schema GPT-4o must return):

**File:** `processing/gold_job.py` lines 151–167

```
You are a financial and geopolitical intelligence analyst.
Given a news article, extract structured intelligence metadata.

Respond ONLY with valid JSON matching this exact schema:
{
  "executive_summary":    "<2-3 sentence synthesis of the article's key intelligence>",
  "key_findings":         ["<finding 1>", "<finding 2>", "<finding 3>"],
  "impact_level":         <integer 1-5, geopolitical/market significance>,
  "urgency_level":        <integer 1-5, time-sensitivity of the signal>,
  "reliability_score":    <float 0.0-1.0, source credibility estimate>,
  "sentiment_score":      <float -1.0 to 1.0, negative=bearish, positive=bullish>,
  "extracted_entities":   ["<person, institution, country, or asset name>", ...],
  "topic_classification": "<one of: Monetary Policy | Geopolitics | Energy | Technology | Health | Other>",
  "fact_check_flag":      <true if any claim requires external verification, else false>,
  "geospatial_focus":     "<primary region or country most affected by this news>"
}
```

**User prompt** (built dynamically per article):

**File:** `processing/gold_job.py` lines 768–796

```python
def build_cognitive_metadata_prompt(silver_doc: dict) -> str:
    title  = silver_doc.get("title", "")
    lead   = silver_doc.get("inverted_pyramid_lead", "")
    body   = silver_doc.get("full_text_raw", "")
    source = silver_doc.get("source_display_name") or silver_doc.get("source_name", "")
    date   = silver_doc.get("publish_date", "")

    return (
        f"Source: {source}\n"
        f"Published: {date}\n\n"
        f"Headline: {title}\n\n"
        f"Lede: {lead}\n\n"
        f"Body: {body}"
    )
```

For our OPEC article it produces:

```
Source: Reuters
Published: 2026-04-09T10:30:00Z

Headline: OPEC cuts oil production by 1 million barrels

Lede: The OPEC+ alliance announced a surprise production cut on Tuesday...

Body: Vienna — Ministers from the OPEC+ group agreed...
```

The API call itself:

**File:** `processing/gold_job.py` lines 823–833

```python
response = client.chat.completions.create(
    model=OPENAI_MODEL_NAME,                           # GPT-4o
    response_format={"type": "json_object"},            # forces valid JSON output
    messages=[
        {"role": "system", "content": _COGNITIVE_METADATA_SYSTEM_PROMPT},
        {"role": "user",   "content": prompt},
    ],
    temperature=0.2,     # near-deterministic — same article → same output
    max_tokens=1024,
)
return parse_openai_consensus_response(response.choices[0].message.content)
```

`temperature=0.2` means "very deterministic." The same article fed twice will produce nearly identical JSON both times.

---

### `apply_impact_boost()` — the +1 rule

**File:** `processing/gold_job.py` lines 836–862

```python
def apply_impact_boost(ai_meta: dict, silver_doc: dict) -> dict:
    if silver_doc.get("impact_boost"):
        current = int(ai_meta.get("impact_level", 3))
        ai_meta["impact_level"] = min(5, current + 1)
    return ai_meta
```

The `impact_boost` flag was set by the producer back in Bronze (a cheap string scan for terms like `"opec"`, `"israel"`, `"energy crisis"`). If flagged, GPT-4o's score of `4` becomes `min(5, 4+1) = 5`. The cap at 5 ensures the Gold schema validator never receives an out-of-range value.

---

### `call_openai_embedding()` — creating the vector

**File:** `processing/gold_job.py` lines 274–293

```python
def call_openai_embedding(text: str, client: Any) -> list[float]:
    response = client.embeddings.create(
        model=EMBEDDING_MODEL,          # "text-embedding-3-small"
        input=text,
    )
    return response.data[0].embedding   # list of 1536 floats
```

The input is `ai_meta["executive_summary"]` — the 2-3 sentence GPT-4o synthesis, not the raw article body. The result is a list of **1536 floating-point numbers** — a point in 1536-dimensional space that encodes the semantic meaning of the article. Two articles about OPEC production cuts will produce vectors that are geometrically close to each other. This is what powers the semantic search in the Researcher agent later.

---

## Step 4 — Persistence: Two Database Writes

After `process_newsapi_gold_message()` returns, the Flink Gold function makes two separate database calls.

### Write 1 — `knowledge_vault.archive()` — the full-text store

**File:** `persistence/knowledge_vault.py` lines 57–153

```python
def archive(silver_doc: dict) -> Optional[str]:
    _validate_record(silver_doc)

    # Dedup guard — if this hash exists, skip silently
    if exists_by_document_hash(silver_doc["document_hash"]):
        return None

    sql = """
        INSERT INTO knowledge_vault (
            document_hash, canonical_event_id, raw_data_ref,
            source_name, author, original_url, publish_date,
            full_text_raw, inverted_pyramid_lead, detected_entities,
            relevance_score, sniper_keywords
        ) VALUES (
            %s, %s, %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
        RETURNING doc_id::text;
    """
    params = (
        doc_hash,
        silver_doc.get("canonical_event_id", ""),
        bronze_ref,
        source_name,
        silver_doc.get("author", ""),
        silver_doc.get("original_url", ""),
        publish_date,
        silver_doc.get("full_text_raw", ""),
        silver_doc.get("inverted_pyramid_lead", ""),
        Json(silver_doc.get("detected_entities", [])),   # JSONB — empty at Silver layer
        float(silver_doc.get("relevance_score", 0.0)),
        Json(silver_doc.get("sniper_keywords", [])),
    )

    with get_cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()

    return row["doc_id"]
```

Note `detected_entities=[]` — empty at this stage. After GPT-4o returns `extracted_entities`, the Flink Gold function calls `update_detected_entities()` to backfill that column on the same row.

---

> **Python syntax — `with` (context managers)**
>
> ```python
> with get_cursor() as cur:
>     cur.execute(sql, params)
>     row = cur.fetchone()
> ```
>
> The `with` statement is Python's way of saying: "open this resource, use it, then automatically clean up when the block ends — even if an error occurs." For a database cursor, "clean up" means committing the transaction and closing the cursor.
>
> Short standalone example:
> ```python
> with open("file.txt") as f:
>     data = f.read()
> # file is automatically closed here, even if read() raised an exception
> ```
> Without `with`, you would need to write `try / finally: f.close()` manually.

---

### Write 2 — `knowledge_vectors.insert()` — the HNSW vector store

**File:** `persistence/knowledge_vectors.py` lines 78–191

```python
def insert(record: dict) -> str:
    meta            = record["metadata"]
    signal_id       = meta.get("signal_id") or str(uuid.uuid4())
    source_platform = meta["source_platform"]                       # "newsapi"
    entry_type      = _ENTRY_TYPE_MAP.get(source_platform, "news_article")
    # _ENTRY_TYPE_MAP = {"newsapi": "news_article", "arxiv": "arxiv_paper", ...}

    embedding = record["embedding"]
    _validate_embedding(embedding)    # ensures exactly 1536 floats

    sql = """
        INSERT INTO knowledge_vectors (
            signal_id, canonical_event_id, silver_data_ref, raw_data_ref,
            source_platform, entry_type, published_at,
            content_vitals, enrichment_ai, domain_context, embedding
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s
        )
        ON CONFLICT (signal_id) DO NOTHING
        RETURNING signal_id;
    """
    with get_connection() as conn:
        register_vector(conn)         # teaches psycopg2 to send list[float] as vector(1536)
        with conn.cursor() as cur:
            cur.execute(sql, params)

    return signal_id
```

**`ON CONFLICT (signal_id) DO NOTHING`** — if Flink replays a message during a restart, this SQL clause silently ignores the duplicate instead of raising an error. This is the database-level idempotency guard (Flink provides exactly-once delivery, but restarts can re-deliver messages).

**`register_vector(conn)`** — from the `pgvector` library. It teaches psycopg2 (the Python PostgreSQL driver) how to convert a Python `list[float]` into the PostgreSQL `vector(1536)` type that the HNSW index understands.

---

## Final Summary

```
1. newsapi_producer.py  _fetch_top_headlines()    → HTTP GET to newsapi.org
2.                       _passes_whitelist()        → source.id in frozenset? ✓
3.                       _passes_keyword_sniper()   → any(kw in text)? ✓ (general only)
4.                       _build_raw_payload()       → article dict + impact_boost flag
5.                       _emit()                    → build_bronze_message()
6. kafka_utils.py        build_bronze_payload()     → Bronze Schema (ingestion_id, raw_payload, …)
7.                       build_envelope()           → outer envelope (event_id, trace_id, …)
8.                       ndjson_serializer()        → JSON bytes + \n → Kafka
                         [Kafka topic: ingest.bronze.newsapi]
9. silver_job.py         process_newsapi_message()  → validate × 3 gates
10.                      snipe_article()            → relevance_score=0.55, is_high_signal=True
11. deduplication.py     hash_document()            → SHA-256 hex → document_hash
12. silver_job.py        map_newsapi_article_to_silver() → Silver record
                         [Kafka topic: process.silver.global_news]
13. gold_job.py          process_newsapi_gold_message() → is_high_signal guard ✓
14.                      call_openai_cognitive_metadata() → GPT-4o → ai_meta dict
15.                      apply_impact_boost()           → impact_level = min(5, 4+1) = 5
16.                      call_openai_embedding()        → list[float] × 1536
17.                      build_gold_global_signal()     → Gold record
                         [Kafka topic: process.gold.global_news]
18. knowledge_vault.py   archive()           → INSERT full-text → returns doc_id
19. knowledge_vectors.py insert()            → INSERT Gold + vector → HNSW-indexed
```
