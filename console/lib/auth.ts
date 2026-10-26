// Sign-in and sessions (TRD §2.0, §3.4, §4.4; code-standards · Next.js): Auth.js runs the GitHub
// App's user-to-server authorisation; the user's token goes AES-GCM encrypted into console.db
// and never into the cookie or a response; sign-out deletes the row and revokes the token.
// Server only: this module opens console.db.

import NextAuth from "next-auth";

import { read } from "./api";
import { authConfig, claim } from "./auth.config";
import { revokeToken, viewerPermission } from "./github";
import { SessionStore, keyFromEnv, type SessionRow } from "./sessions";

let storeSingleton: SessionStore | null = null;

export function sessions(): SessionStore {
  if (!storeSingleton) {
    storeSingleton = new SessionStore(process.env.CONSOLE_DB_PATH ?? "console.db", keyFromEnv());
  }
  return storeSingleton;
}

async function repository(): Promise<{ owner: string; repo: string }> {
  const installation = await read("/api/v1/installation", { caller: null });
  return { owner: installation.org, repo: installation.repo };
}

export const { handlers, auth, signIn, signOut } = NextAuth({
  ...authConfig,
  callbacks: {
    ...authConfig.callbacks,
    async jwt({ token, account, profile }) {
      const login = profile && typeof profile.login === "string" ? profile.login : undefined;
      if (account?.access_token && login) {
        const permission = await viewerPermission(account.access_token, await repository(), login);
        const row = sessions().create(login, permission, account.access_token);
        return { ...token, sid: row.sid, login, permission };
      }
      return token;
    },
  },
  events: {
    async signOut(message) {
      const token = "token" in message ? message.token : null;
      const sid = token ? claim(token as Record<string, unknown>, "sid") : undefined;
      if (!sid) return;
      const store = sessions();
      const userToken = store.token(sid);
      store.delete(sid);
      const clientId = process.env.AUTH_GITHUB_ID;
      const clientSecret = process.env.AUTH_GITHUB_SECRET;
      if (userToken && clientId && clientSecret) {
        await revokeToken(clientId, clientSecret, userToken);
      }
    },
  },
});

/** The live console session behind the request's cookie, or null (BFF step two). */
export async function loadSession(): Promise<SessionRow | null> {
  const session = await auth();
  if (!session?.sid) return null;
  return sessions().get(session.sid);
}

/** The signed-in user's GitHub token for a write under their login; server only. */
export async function userToken(): Promise<string | null> {
  const session = await auth();
  if (!session?.sid) return null;
  return sessions().token(session.sid);
}
