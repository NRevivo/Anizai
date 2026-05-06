import {
    createUserWithEmailAndPassword,
    onAuthStateChanged,
    signInWithEmailAndPassword,
    signInWithPopup,
    signOut,
    updateProfile,
    type User,
} from 'firebase/auth';

import { auth, googleProvider } from '../lib/firebase';

export interface SignUpInput {
    name: string;
    email: string;
    password: string;
}

export async function signInWithEmail(email: string, password: string): Promise<User> {
    const credential = await signInWithEmailAndPassword(auth, email, password);
    return credential.user;
}

export async function signInWithGoogle(): Promise<User> {
    const credential = await signInWithPopup(auth, googleProvider);
    return credential.user;
}

export async function signUpWithEmail(input: SignUpInput): Promise<User> {
    const credential = await createUserWithEmailAndPassword(auth, input.email, input.password);
    if (input.name) {
        await updateProfile(credential.user, { displayName: input.name });
    }
    return credential.user;
}

export async function signOutUser(): Promise<void> {
    await signOut(auth);
}

export function subscribeToAuthState(callback: (user: User | null) => void): () => void {
    return onAuthStateChanged(auth, callback);
}

export function describeAuthError(error: unknown): string {
    if (error && typeof error === 'object' && 'code' in error && typeof (error as { code: unknown }).code === 'string') {
        const code = (error as { code: string }).code;
        switch (code) {
            case 'auth/invalid-email':
                return 'That email address is not valid.';
            case 'auth/user-disabled':
                return 'This account has been disabled. Contact support.';
            case 'auth/user-not-found':
            case 'auth/wrong-password':
            case 'auth/invalid-credential':
                return 'Email or password is incorrect.';
            case 'auth/email-already-in-use':
                return 'An account already exists with that email.';
            case 'auth/weak-password':
                return 'Password should be at least 6 characters.';
            case 'auth/popup-closed-by-user':
                return 'Sign-in was cancelled before it completed.';
            case 'auth/network-request-failed':
                return 'Network error. Check your connection and try again.';
            default:
                return `Authentication failed (${code}).`;
        }
    }
    return error instanceof Error ? error.message : 'Authentication failed.';
}
