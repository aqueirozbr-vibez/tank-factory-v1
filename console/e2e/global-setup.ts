// The Playwright run signs in without GitHub: it creates a console.db session row the way
// sign-in does (lib/sessions.ts) and mints the Auth.js cookie that names it, so the console
// under test sees a signed-in login. Needs AUTH_SECRET, CONSOLE_SESSION_KEY and CONSOLE_DB_PATH
// set to the same values the console was started with. Nothing here touches GitHub.
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";

import { encode } from "next-auth/jwt";

import { SESSION_HOURS } from "../lib/session-constants";
import { SessionStore, keyFromEnv } from "../lib/sessions";

export const COOKIE = "authjs.session-token";
export const STATE_PATH = path.resolve("e2e/.auth/state.json");

export default async function globalSetup(): Promise<void> {
  const secret = process.env.AUTH_SECRET;
  if (!secret) throw new Error("AUTH_SECRET is required to mint the e2e session");
  const login = process.env.E2E_LOGIN ?? "mlima";
  const permission = process.env.E2E_PERMISSION ?? "write";
  const store = new SessionStore(process.env.CONSOLE_DB_PATH ?? "console.db", keyFromEnv());
  const row = store.create(login, permission, "e2e-placeholder-not-a-github-token");
  store.close();
  const value = await encode({
    token: { sid: row.sid, login, permission },
    secret,
    salt: COOKIE,
    maxAge: SESSION_HOURS * 3600,
  });
  const base = new URL(process.env.CONSOLE_URL ?? "http://127.0.0.1:3000");
  const state = {
    cookies: [
      {
        name: COOKIE,
        value,
        domain: base.hostname,
        path: "/",
        expires: Math.floor(Date.now() / 1000) + SESSION_HOURS * 3600,
        httpOnly: true,
        secure: false,
        sameSite: "Lax" as const,
      },
    ],
    origins: [],
  };
  mkdirSync(path.dirname(STATE_PATH), { recursive: true });
  writeFileSync(STATE_PATH, JSON.stringify(state));
}
