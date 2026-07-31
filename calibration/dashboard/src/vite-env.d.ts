/// <reference types="vite/client" />

/*
 * The env vars this app reads. Typed explicitly rather than left as an index
 * signature so a typo in a variable name is a compile error rather than a
 * silently undefined value that surfaces as "Firebase is not configured".
 */
interface ImportMetaEnv {
  readonly VITE_FIREBASE_API_KEY?: string
  readonly VITE_FIREBASE_AUTH_DOMAIN?: string
  readonly VITE_FIREBASE_PROJECT_ID?: string
  readonly VITE_FIREBASE_APP_ID?: string
  /** Local development bypass — the API only honours it against the emulator. */
  readonly VITE_DEV_OPERATOR_EMAIL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
