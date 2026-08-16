# verification-queue-20260816.md — three checks, all closed

> Domain: C — Cloud
> Type: Verification record (closed)
> Raised: 2026-08-16 by the operational-guide procedure audit
> Closed: 2026-08-16 by the concurrent operating session
> **Status: ✅ all three answered. No action outstanding.**

## Why this file exists

The 2026-08-16 audit of the five operational guides checked every runnable
command against `infrastructure/k8s/`, the DAG files and the agent source. Three
findings could not be settled that way — each depended on a fact that existed
only in the running system. They were written up as command → expected output →
decision rule so they could be executed by a different session, and they were.

**It is kept as a record rather than deleted, because two of the three answers
are counter-intuitive and the reasoning that produced them is reusable.** One
inference was wrong in a way worth understanding; one was a near-miss that
looked like luck and was.

| # | Question | Answer | Landed in |
|---|---|---|---|
| 1 | Did the Grafana pod predate the password fix? | **No — by 24 seconds** | `CLOUD_CONNECTION_GUIDE.md` §1.4 |
| 2 | Does `NEWSAI_API_KEY` hold an eventregistry key? | **Yes, confirmed twice** | KG-C-5 |
| 3 | Does the OpenAI rate-limit alert exist here? | **Yes — the record was the defect** | KG-C-17, carry-over |

---

## 1. Grafana pod vs. secret version — ✅ no action needed, mechanism confirmed real

**The question.** `GRAFANA_ADMIN_PASSWORD` was stored malformed and fixed on
2026-08-15 by uploading a trimmed value as version 2 (KG-C-14). The CSI driver
does not rotate a mounted secret into a running pod, and Grafana reads the
password once at container start. If the pod predated the fix it would still be
authenticating against version 1, and the correct password would 401.

**The answer — it did not bite, but only just.**

```
secret version 2 created   15:48:50
grafana pod started        15:49:14      ← 24 seconds later
```

A rollout restart happened to be part of the KG-C-14 fix. Verified
behaviourally rather than inferred from timestamps: the current password returns
**200**, the old space-prefixed value **401**, and a deliberately wrong value
**401** — which also rules out the pod having authentication disabled.

**What was kept, and why it was reworded.** The guide note stays, but it no
longer says *"this pod may predate the fix"* — that framing expires the moment
the question is answered, and would have read as noise on the next rotation.
It now states the general rule:

> **A CSI-mounted secret is read at container start and does not take effect
> until the pod restarts. A rotation is not complete until you restart the pod.**

The 24-second margin was luck, not design. The next rotation performed without a
restart will hit this, and the symptom — a 401 with a password you can prove is
correct — is one of the least obvious failures in the stack.

---

## 2. `NEWSAI_API_KEY` provider — ✅ confirmed eventregistry, finding fully upheld

**The question.** KG-C-5 claimed the secret held a *thenewsapi.com* key and that
a newsapi.ai key must never be rotated in. The code says the opposite: Phase 7A
(2026-05-09) moved the producer to newsapi.ai / Event Registry, and
`config/settings.py` sets `NEWSAI_BASE_URL = "https://eventregistry.org/api/v1"`.
The repo could establish what the producer *needs*, not what the secret *holds*.

**The answer — confirmed on two independent lines.**

- **Shape.** 36 bytes, UUID `8-4-4-4-12` form. That is Event Registry's key
  format. TheNewsAPI issues ~60-character dashless tokens, so the two are not
  confusable by length or by shape — this is a stronger discriminator than a
  byte count alone.
- **Behaviour.** The live producer receives **HTTP 200** from
  `eventregistry.org/api/v1/article/getArticles`.

The secret is also clean of the KG-C-14 whitespace/quote defect.

**Consequence.** The original §2.5 instruction — *"do not rotate a newsapi.ai key
into it"* — would have broken NewsAPI ingestion if followed, with KG-A-23
masking the DAG failure. Both the KG-C-5 row and the guide are corrected;
`VALIDATION_GUIDE.md` had it right throughout and is the reference statement.

**Still open, and deliberately not decided here:** whether to rename the secret
at all. `THE_NEWS_API_KEY` is the wrong target — it names the retired provider.
The existing name already matches the current one, so doing nothing is
defensible. **Ron's call.** KG-C-1a's *"pairs with KG-C-5's rename"* line is left
as written and is noted as dangling; that rebuild stands on its own
hackernews-cadence justification regardless.

---

## 3. OpenAI rate-limit alert — ✅ exists; the missing record was the real defect

**The question.** `cluster_operations_guide.md` §5.4 sends an operator to Cloud
Monitoring for an `OpenAI rate-limit (WARNING)` alert. That alert is deliberately
not a Prometheus rule — it is a Cloud Logging metric feeding Cloud Monitoring,
provisioned by `infrastructure/gcp/06_monitoring_setup.sh`.
`carryover-20260815-migration.md` contained no record of that script being
re-run against `anizai-pipehub`, while eight of the nine `gcp/0*.sh` scripts had
visible migration evidence.

**The answer — the script ran; the carry-over never said so.**

```
metric    openai_rate_limit_errors        present in anizai-pipehub
policies  WARNING + CRITICAL (storm)      both enabled
delivery  ron.mintz21@gmail.com           wired, not orphaned
```

**Why this is still worth a permanent record.** The inference was drawn from an
absence in the migration record, and absence is exactly the class of evidence
that cannot be settled by reading the repo — the same shape as the
*absence cannot be grepped* lesson on KG-C-11, arrived at independently. The
reasoning was sound and only the conclusion was wrong.

Had it gone the other way, the failure would have been severe: an empty Cloud
Monitoring console reads as *"no rate-limit problem"* during precisely the week
KG-C-1a projects 9–14k calls/day against a 10k Tier-1 ceiling, and KG-C-16 means
Grafana could not have answered the question either.

**The durable fix is documentary and is applied.** The carry-over now records
that `06` ran and what it provisioned, and §5.4's warning was rewritten from
*"this may not exist"* to the verified metric name — so the triage step now tells
an operator what to look for instead of what to doubt.

> **Standing rule:** a migration carry-over must record **every provisioning
> script that ran**, not only the ones whose output a gate happened to test.
> S6 tested the forecast path and S7 the backup path. Nothing tested alerting —
> so nothing wrote it down, and the silence read as absence.

---

## Closing note

All three are struck. The two lessons that outlive the answers are the CSI
rotate-then-restart rule (item 1) and the carry-over completeness rule (item 3);
both are now stated where they will be read — in the guide and in the carry-over
respectively, not only here.
