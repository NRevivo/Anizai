# Anizai Server

Backend API server for the Anizai forecasting platform.

## Tech Stack

- **Runtime**: Node.js 20 LTS
- **Language**: TypeScript
- **Framework**: Express
- **Auth**: Firebase Admin SDK (ADC)
- **Validation**: Zod
- **Logging**: Pino

## Security Note

> **This project intentionally does NOT use service account key files.**
>
> We use Application Default Credentials (ADC) for secure, team-friendly authentication.
> No private keys are stored in environment variables or the repository.

## Getting Started

### Prerequisites

- Node.js 20+
- npm
- Google Cloud CLI (`gcloud`)
- Access to the Firebase/GCP project

### Installation

```bash
npm install
cp .env.example .env
```

### Local Development Setup

Each developer must authenticate once:

```bash
# Install Google Cloud CLI (if not installed)
brew install --cask google-cloud-sdk

# Login to Google Cloud
gcloud auth login

# Set up Application Default Credentials
gcloud auth application-default login

# Set the project
gcloud config set project anizai-ai
```

> **Note**: You must have appropriate IAM permissions on the project (e.g., `Firebase Admin`, `Firestore User`).

Then start the server:

```bash
npm run dev
```

Server runs at `http://localhost:3000`

### Production Setup

When deployed to Cloud Run, Firebase, or GCP:

- The backend automatically uses the attached service account
- No environment secrets needed for Firebase auth
- Only `FIREBASE_PROJECT_ID` must be set

## Emulator Testing

Test endpoints locally using Firebase Emulators without a frontend.

1. **Start Emulators**:
   ```bash
   cd firebase && firebase emulators:start
   ```

2. **Start Backend (Emulator Mode)**:
   ```bash
   npm run dev:emu
   ```
   *Connects to Auth (9099) and Firestore (8080)*

3. **Get ID Token**:
   ```bash
   npm run emu:token
   ```
   *Creates/signs in a test user (`noam@gmail.com`) and prints the ID token.*

4. **Test /me Endpoint**:
   ```bash
   npm run emu:test:me
   ```
   *Gets a token and calls `GET /me` with it.*

## API Endpoints

### Health Check (Public)

```bash
curl http://localhost:3000/health
```

### Get Current User (Protected)

```bash
curl http://localhost:3000/me \
  -H "Authorization: Bearer <firebase-id-token>"
```

## Project Structure

```
server/
├── src/
│   ├── index.ts              # Entry point
│   ├── server.ts             # Express app factory
│   ├── config/env.ts         # Environment config
│   ├── lib/
│   │   ├── logger.ts         # Pino logger
│   │   └── firebase.ts       # Firebase Admin (ADC)
│   ├── middleware/
│   │   ├── requestId.ts
│   │   ├── auth.ts           # Token verification
│   │   └── error.ts
│   ├── routes/
│   │   ├── health.ts
│   │   └── user.ts
│   └── types/api.ts
└── tests/
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `PORT` | No | Server port (default: 3000) |
| `NODE_ENV` | No | development / production / test |
| `FIREBASE_PROJECT_ID` | Yes | Firebase/GCP project ID |

## Scripts

| Script | Description |
|--------|-------------|
| `npm run dev` | Start dev server with hot reload |
| `npm run build` | Compile TypeScript |
| `npm start` | Run production build |
| `npm run lint` | Run ESLint |
| `npm test` | Run tests |

## Troubleshooting

### "Could not load the default credentials"

Run:
```bash
gcloud auth application-default login
```

### "Permission denied" errors

Ensure your Google account has the required IAM roles:
- `Firebase Admin SDK Administrator Service Agent`
- `Cloud Datastore User` (for Firestore)

## License

MIT
