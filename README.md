# Anizai

RAG-based event-forecasting platform with a React client, an Express BFF, and a Kafka/Flink data pipeline feeding a LangGraph forecasting agent.

![Anizai Hero](docs/images/landing-hero.png)

## Overview

A user asks a future-oriented question ("Will X happen before Y?") and receives a
structured forecast: probability, confidence, key drivers, a reasoning chain, and an
evidence trail.

Anizai is organized as a monorepo with three main parts:

- `client/`: React + TypeScript SPA (dashboard, sessions, trending, auth flows)
- `server/`: Express + TypeScript **BFF** — auth, session CRUD, idempotency, usage
  charging, and Firestore reads. It is a mediator: it does not call OpenAI, does not
  query the vector store, and does not generate forecasts.
- `data-pipeline/`: Python — Kafka + Flink medallion pipeline (Bronze → Silver → Gold)
  into PostgreSQL/pgvector, plus the LangGraph agent that produces the forecasts.

The two halves meet in Firestore: the BFF writes a queue document, the agent claims it,
runs its graph, and writes the result back for the client to read.

## Documentation

| Area | Start here |
|---|---|
| Client + BFF (`client/`, `server/`) | [`docs/C_frontend/frontend_overview.md`](docs/C_frontend/frontend_overview.md) |
| Data pipeline (`data-pipeline/`) | [`data-pipeline/docs/A_pipeline/pipeline_overview.md`](data-pipeline/docs/A_pipeline/pipeline_overview.md) |

Historical task logs and audits live at the top level of `docs/` and are indexed —
with accuracy warnings where they have gone stale — in
[`docs/C_frontend/frontend_archive.md`](docs/C_frontend/frontend_archive.md).

## Tech Stack

### Client

- React 19
- TypeScript
- Vite
- Tailwind CSS
- Firebase Web SDK
- Recharts

### Server

- Node.js (>= 20)
- TypeScript
- Express
- Firebase Admin SDK
- Zod
- Pino
- Vitest

### Data Pipeline

- Python 3.11
- Kafka (`kafka-python`)
- Apache Flink / PyFlink 1.19 (runs in Docker; not installed in the local venv)
- PostgreSQL + pgvector + TimescaleDB (`psycopg2`, `pgvector`)
- OpenAI (GPT-4o, `text-embedding-3-small`) + LangGraph
- Airflow (scheduled producers)

## Repository Structure

```text
.
├── client/                  # Frontend (Vite + React + TS)
├── server/                  # BFF (Express + TS)
│   ├── firebase/            # Firestore rules + indexes
│   ├── scripts/             # Seed, probe, emulator, migration scripts
│   └── tests/               # Vitest suites
├── data-pipeline/           # Ingestion/streaming pipeline + LangGraph agent (Python)
│   └── docs/A_pipeline/     # Pipeline documentation
├── docs/
│   ├── C_frontend/          # Client + BFF documentation
│   ├── backend-specs/       # Cross-team data contracts
│   ├── audits/              # Historical audits
│   └── images/              # README and product images
└── README.md
```

## Prerequisites

- Node.js 20+
- npm
- Python 3.10+ (for `data-pipeline`)
- Docker + Docker Compose (optional, for Kafka infra)

## Setup

Required Firebase console steps (one-time, per Firebase project):

1. **Authentication → Sign-in method**: enable **Email/Password** and **Google** providers.
2. **Firestore Database**: create a Firestore instance and deploy the rules from `server/firebase/firestore.rules`.
3. From the Firebase console (Project settings → General → Your apps → Web app), copy the SDK config values into the env files below.

Local environment files (never committed; use the `.env.example` files as templates):

```bash
cp client/.env.example client/.env   # fill VITE_FIREBASE_* and VITE_API_BASE_URL
cp server/.env.example server/.env   # fill FIREBASE_PROJECT_ID
```

Then follow the Quick Start below.

## Quick Start

### 1. Clone

```bash
git clone <your-repo-url>
cd Anizai
```

### 2. Client Setup

```bash
cd client
npm install
cp .env.example .env
npm run dev
```

Client runs at `http://localhost:5173`.

### 3. Server Setup

In a new terminal:

```bash
cd server
npm install
cp .env.example .env
npm run dev
```

Server runs at `http://localhost:3000`.

## Environment Variables

### Client (`client/.env`)

Copy from `client/.env.example`:

- `VITE_FIREBASE_API_KEY`
- `VITE_FIREBASE_AUTH_DOMAIN`
- `VITE_FIREBASE_PROJECT_ID`
- `VITE_FIREBASE_STORAGE_BUCKET`
- `VITE_FIREBASE_MESSAGING_SENDER_ID`
- `VITE_FIREBASE_APP_ID`
- `VITE_API_BASE_URL` (default: `/api`)

### Server (`server/.env`)

Copy from `server/.env.example`:

- `PORT` (default: `3000`)
- `NODE_ENV` (`development`, `production`, `test`)
- `FIREBASE_PROJECT_ID` (required)
- `ALLOW_DEMO_ROUTES` (`true` / `false`, default off) — must be `true` **and**
  `NODE_ENV=development` for the `/demo/*` routes to mount at all
- Optional emulator vars:
  - `FIREBASE_AUTH_EMULATOR_HOST`
  - `FIRESTORE_EMULATOR_HOST`

## API Endpoints (Current)

Routes are mounted at the server root — there is no `/api` prefix on the server. The
client prefixes `/api` and the Vite dev proxy strips it. See
[`frontend_api.md`](docs/C_frontend/frontend_api.md) §7.3.

Public:

- `GET /`
- `GET /health`
- `GET /trending` (`?limit`, default 20, max 100)

Protected (Firebase ID token required):

- `GET /me`
- `PATCH /me/plan`
- `GET /sessions`
- `GET /sessions/:id`
- `POST /sessions` — requires a UUID `idempotencyKey`
- `POST /sessions/:id/messages`
- `POST /sessions/:id/clarify` — only when status is `awaiting_clarification`
- `POST /sessions/:id/retry` — only when status is `failed`
- `DELETE /sessions/:id`

Development only (both gates required, see `ALLOW_DEMO_ROUTES`):

- `GET /demo/sessions`, `GET /demo/sessions/:id`, `GET /demo/user`

Full route matrix with validation, status codes, and error codes:
[`frontend_api.md`](docs/C_frontend/frontend_api.md) §3.

## Development Commands

### Client

```bash
cd client
npm run dev
npm run build
npm run test     # vitest
npm run lint     # currently fails — see note below
npm run preview
```

> **Known issue:** `npm run lint` in `client/` fails with
> `Cannot find package '@eslint/js'` — the root `eslint.config.js` imports ESLint
> packages that are installed per-package rather than at the repo root. Typecheck and
> tests are unaffected. Tracked as KG-C-2 in
> [`frontend_sprints.md`](docs/C_frontend/frontend_sprints.md) §4.

### Server

```bash
cd server
npm run dev
npm run build
npm start
npm run lint
npm run test
```

Emulator helpers:

```bash
cd server
npm run dev:emu             # run against the Firebase emulators
npm run emu:token
npm run emu:test:me
npm run test:session-result
npm run seed                # seed one fully-populated forecast session
npm run seed:clean -- --yes # remove it
npm run probe:all-sessions  # read-only inventory of the sessions collection
```

## Data Pipeline

Owned by a separate track — see
[`pipeline_overview.md`](data-pipeline/docs/A_pipeline/pipeline_overview.md) for the
authoritative documentation. Quick local start below.

Install dependencies:

```bash
cd data-pipeline
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Start Kafka infrastructure:

```bash
cd data-pipeline/infrastructure
docker compose up -d
```

Run a producer (nine are available in `data-pipeline/ingestion/`):

```bash
cd data-pipeline
python ingestion/newsapi_producer.py
```

## One-time: migrate legacy demo-user data

The original frontend signed every login in as a hardcoded `demo-user-001`. After real auth was restored, any sessions seeded under that UID become invisible to real users (server enforces ownership). The `migrate:demo-data` script reassigns ownership of every demo-owned Firestore document to a real Firebase Auth user.

It is **dry-run by default**. Always run without `--apply` first and inspect the report.

```bash
cd server

# Option A — create a fresh Firebase Auth user as part of the migration:
npm run migrate:demo-data -- --email=viewer@anizai.local --password=<pwd>           # dry-run
npm run migrate:demo-data -- --email=viewer@anizai.local --password=<pwd> --apply   # commit

# Option B — migrate to a UID you already created (e.g. via Google sign-in in the UI):
npm run migrate:demo-data -- --target-uid=<existingUid>            # dry-run
npm run migrate:demo-data -- --target-uid=<existingUid> --apply    # commit

# Override the source UID if it differs from the default `demo-user-001`:
npm run migrate:demo-data -- --target-uid=... --demo-uid=<otherUid> --apply
```

The script:
- Updates `userId` in `sessions`, `sessionResults`, `forecastQueries`, and `sessions/*/messages` (the only collections with that field).
- Leaves `users/demo-user-001` and the legacy demo Auth user in place in case other artifacts still reference them.
- Is idempotent — re-running on already-migrated docs is a no-op.

This is a one-time operation tied to the legacy seed data; remove it once it has run successfully against every environment that needs it.

## Screenshots

![How It Works](docs/images/how-it-works.png)
![Plan Selection](docs/images/plan-selection.png)
![Dashboard Desktop](docs/images/dashboard-desktop.png)
![Dashboard Mobile](docs/images/dashboard-mobile.png)

## Status

Client, BFF, and data pipeline are all implemented and running end to end. Current
state per area:

- **Client + BFF** — no sprint currently open. Known gaps are tracked as `KG-C-*` in
  [`frontend_sprints.md`](docs/C_frontend/frontend_sprints.md) §4.
- **Data pipeline** — fully implemented and operationally closed; the one open item is
  filter-threshold calibration. See
  [`pipeline_sprints.md`](data-pipeline/docs/A_pipeline/pipeline_sprints.md).
- **Not yet live in the product** — market comparison and sentiment time series (the
  agent emits nothing for them yet), and the live agent reasoning trace.

## License

MIT
