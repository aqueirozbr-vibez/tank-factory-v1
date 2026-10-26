// /auth/signin, /auth/callback/github, /auth/signout: Auth.js route handlers (TRD §5.1).
import { handlers } from "@/lib/auth";

export const { GET, POST } = handlers;
