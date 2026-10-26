// The edge-safe half of the Auth.js configuration (TRD §2.0 Sign-in; §5.1 /auth/*): the GitHub
// App's user-to-server authorisation as the provider (client id and secret from AUTH_GITHUB_ID
// and AUTH_GITHUB_SECRET), an 8-hour JWT cookie that carries only the console session id, login
// and permission. The node half (lib/auth.ts) adds the session store.

import type { NextAuthConfig } from "next-auth";
import GitHub from "next-auth/providers/github";

import { SESSION_HOURS } from "./session-constants";

declare module "next-auth" {
  interface Session {
    sid?: string;
    login?: string;
    permission?: string;
  }
}

export const PUBLIC_PATHS = ["/auth", "/unsupported", "/api/console/health"];

export function isPublicPath(pathname: string): boolean {
  return PUBLIC_PATHS.some((p) => pathname === p || pathname.startsWith(`${p}/`));
}

/** A JWT claim as a string, or undefined; the token's claims are untyped. */
export function claim(token: Record<string, unknown>, name: string): string | undefined {
  const value = token[name];
  return typeof value === "string" && value ? value : undefined;
}

export const authConfig: NextAuthConfig = {
  basePath: "/auth",
  trustHost: true,
  session: { strategy: "jwt", maxAge: SESSION_HOURS * 3600 },
  providers: [
    GitHub({
      // a GitHub App's user-to-server token has the App's permissions ∩ the user's; no scopes
      authorization: { params: { scope: "" } },
    }),
  ],
  callbacks: {
    authorized({ auth, request }) {
      if (isPublicPath(request.nextUrl.pathname)) return true;
      return Boolean(auth?.sid);
    },
    session({ session, token }) {
      const claims = token as Record<string, unknown>;
      const sid = claim(claims, "sid");
      const login = claim(claims, "login");
      const permission = claim(claims, "permission");
      if (sid) session.sid = sid;
      if (login) session.login = login;
      if (permission) session.permission = permission;
      return session;
    },
  },
};
