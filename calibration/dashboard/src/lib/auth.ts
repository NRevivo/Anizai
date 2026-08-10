/*
 * Firebase Auth for the dashboard.
 *
 * Reuses the `anizai-ai` Firebase project so operators sign in with an account
 * that already exists. Note what that means: a valid token here proves the
 * person is a real product user, **not** that they are an operator. The
 * allowlist on the API is the actual authorisation gate, and this module
 * deliberately does not duplicate it — a client-side allowlist would be
 * decorative, and having two would let them disagree.
 *
 * Config comes from Vite env vars. When they are absent the module reports
 * `configured: false` and the UI says so plainly, rather than rendering a
 * sign-in button that throws on click.
 */

import { setTokenProvider } from './api'

export interface FirebaseUser {
  email: string | null
  uid: string
}

const config = {
  apiKey: import.meta.env.VITE_FIREBASE_API_KEY as string | undefined,
  authDomain: import.meta.env.VITE_FIREBASE_AUTH_DOMAIN as string | undefined,
  projectId: import.meta.env.VITE_FIREBASE_PROJECT_ID as string | undefined,
  appId: import.meta.env.VITE_FIREBASE_APP_ID as string | undefined,
}

export function isConfigured(): boolean {
  return Boolean(config.apiKey && config.authDomain && config.projectId)
}

type Listener = (user: FirebaseUser | null) => void

let currentUser: FirebaseUser | null = null

/*
 * Firebase is imported lazily so the dashboard boots — and its tests run —
 * without the SDK being initialised. An operator tool that cannot render its
 * own "not configured" message because the auth SDK failed to load would be
 * impossible to diagnose.
 */
export async function initAuth(onChange: Listener): Promise<void> {
  if (!isConfigured()) {
    onChange(null)
    return
  }

  const { initializeApp } = await import('firebase/app')
  const { getAuth, onAuthStateChanged, GoogleAuthProvider, signInWithPopup, signOut } =
    await import('firebase/auth')

  const app = initializeApp(config as Record<string, string>)
  const auth = getAuth(app)

  setTokenProvider(async () => {
    const user = auth.currentUser
    // Fetched per request — Firebase rotates the token hourly, and a cached
    // one produces a 401 that looks like a permissions problem.
    return user ? await user.getIdToken() : null
  })

  signIn = async () => {
    await signInWithPopup(auth, new GoogleAuthProvider())
  }
  signOutNow = async () => {
    await signOut(auth)
  }

  onAuthStateChanged(auth, (user) => {
    currentUser = user ? { email: user.email, uid: user.uid } : null
    onChange(currentUser)
  })
}

export let signIn: () => Promise<void> = async () => {
  throw new Error('Firebase is not configured')
}

export let signOutNow: () => Promise<void> = async () => {
  throw new Error('Firebase is not configured')
}

export function getCurrentUser(): FirebaseUser | null {
  return currentUser
}

/*
 * Development bypass: sends the operator's email as the bearer token, which
 * the API accepts only when CALIBRATION_DEV_AUTH=1 *and* it is pointed at the
 * Firestore emulator. Both gates live on the server, so setting this in a
 * client build cannot weaken a real deployment.
 */
export function useDevAuth(email: string): void {
  setTokenProvider(async () => email)
  currentUser = { email, uid: 'dev' }
}
