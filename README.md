# Anizai

AI-powered forecasting platform with a React client, an Express API, and an experimental data pipeline for event/news ingestion.

![Anizai Hero](docs/images/landing-hero.png)

## Overview

Anizai is organized as a monorepo with three main parts:

- `client/`: React + TypeScript web app (dashboard, sessions, trending, auth flows)
- `server/`: Express + TypeScript backend API with Firebase-authenticated endpoints
- `data-pipeline/`: Python ingestion and streaming infrastructure (Kafka-first, Spark/Delta planned)

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

- Python
- Kafka (`kafka-python`)
- Spark + Delta (`pyspark`, `delta-spark`)
- OpenAI + LangChain
- ChromaDB

## Repository Structure

```text
.
├── client/            # Frontend (Vite + React + TS)
├── server/            # Backend API (Express + TS)
├── data-pipeline/     # Ingestion/streaming pipeline (Python)
├── docs/images/       # README and product images
└── README.md
```

## Prerequisites

- Node.js 20+
- npm
- Python 3.10+ (for `data-pipeline`)
- Docker + Docker Compose (optional, for Kafka infra)

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
- `FIREBASE_PROJECT_ID`
- Optional emulator vars:
  - `FIREBASE_AUTH_EMULATOR_HOST`
  - `FIRESTORE_EMULATOR_HOST`

## API Endpoints (Current)

Public:

- `GET /`
- `GET /health`
- `GET /trending`

Protected (Firebase ID token required):

- `GET /me`
- `PATCH /me/plan`
- `GET /sessions`
- `GET /sessions/:id`
- `POST /sessions`
- `POST /sessions/:id/messages`
- `DELETE /sessions/:id`

## Development Commands

### Client

```bash
cd client
npm run dev
npm run build
npm run lint
npm run preview
```

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
npm run dev:emu
npm run emu:token
npm run emu:test:me
npm run test:session-result
```

## Data Pipeline (Experimental)

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

Run the news producer:

```bash
cd data-pipeline
python ingestion/news_producer.py
```

## Screenshots

![How It Works](docs/images/how-it-works.png)
![Plan Selection](docs/images/plan-selection.png)
![Dashboard Desktop](docs/images/dashboard-desktop.png)
![Dashboard Mobile](docs/images/dashboard-mobile.png)

## Status

Current repository includes an active frontend, backend, and early-stage ingestion pipeline. Some pipeline/orchestration components are scaffolded and still under active development.

## License

MIT
