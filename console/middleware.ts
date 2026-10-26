// Every screen needs a session; /auth/*, /unsupported and the health probe stay public
// (TRD §5.1). The edge runtime runs only the cookie half of Auth.js (lib/auth.config.ts).
import NextAuth from "next-auth";

import { authConfig } from "@/lib/auth.config";

export const { auth: middleware } = NextAuth(authConfig);

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
