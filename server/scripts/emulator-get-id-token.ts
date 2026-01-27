// Configuration
const AUTH_URL = process.env.EMULATOR_AUTH_URL || 'http://localhost:9099';
const API_KEY = process.env.EMULATOR_API_KEY || 'fake-api-key';
const EMAIL = process.env.TEST_USER_EMAIL || 'noam@gmail.com';
const PASSWORD = process.env.TEST_USER_PASSWORD || '123456';

async function main() {
    // 1. Try to sign up (create user)
    try {
        await fetch(`${AUTH_URL}/identitytoolkit.googleapis.com/v1/accounts:signUp?key=${API_KEY}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                email: EMAIL,
                password: PASSWORD,
                returnSecureToken: true,
            }),
        });
        // Ignore error if user already exists
    } catch (err) {
        // Proceed to sign in
    }

    // 2. Sign in to get ID token
    const signInRes = await fetch(
        `${AUTH_URL}/identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key=${API_KEY}`,
        {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                email: EMAIL,
                password: PASSWORD,
                returnSecureToken: true,
            }),
        }
    );

    if (!signInRes.ok) {
        const errorText = await signInRes.text();
        console.error(`Failed to sign in: ${signInRes.status} ${signInRes.statusText}`);
        console.error(errorText);
        process.exit(1);
    }

    const data = (await signInRes.json()) as { idToken: string };

    // Print ONLY the token to stdout
    console.log(data.idToken);
}

main().catch((err) => {
    console.error(err);
    process.exit(1);
});
