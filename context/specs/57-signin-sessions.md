# Unit 57: Sign-in and sessions

## Goal

Auth.js with the GitHub App's user-to-server authorisation; `console.db`
sessions with the user's token AES-GCM encrypted, eight hours; the
permission lookup at sign-in; the BFF handler skeleton (validate, session,
authz, act, error body); sign-out revokes the token (PRD §Capability 6;
TRD §2.0 Sign-in, §3.4, §4 `console.db`, §4.4, §5.1 `/auth/*`, §6.2;
code-standards · Next.js).

## Design

Terminal only. `lib/auth.config.ts` is the edge-safe half of the Auth.js
configuration: the GitHub provider with the App's client id and secret
(`AUTH_GITHUB_ID`, `AUTH_GITHUB_SECRET`; a user-to-server token carries the
App's permissions intersected with the user's, no scopes), `basePath`
`/auth`, an 8-hour JWT cookie that carries only the console session id,
login and permission, and the `authorized` rule that keeps `/auth/*`,
`/unsupported` and the health probe public. `lib/auth.ts` adds the node
half: at sign-in the `jwt` callback looks the login's repository
permission up with the user's own token, stores the token AES-GCM
encrypted in `console.db` (`lib/sessions.ts`: `SessionStore` over
`node:sqlite`, one row `sid, login, permission, token_enc, created_at,
expires_at`, deleted at expiry on sight and by `sweep`, key from
`CONSOLE_SESSION_KEY`) and puts the `sid` in the JWT; `loadSession()` and
`userToken()` are the two server-only readers; the `signOut` event deletes
the row and revokes the token at GitHub with the App's client credentials
(`lib/github.ts`: `addLabel`, `removeLabel`, `viewerPermission`,
`revokeToken`; refusals name the fix). `middleware.ts` runs the cookie half
on every route. `lib/bff.ts` is the handler skeleton every `/api/console`
route follows in order: zod body, session, `authz.ts`, act, the standard
error body; a GitHub refusal becomes `github_refused` with its status. A
user with no repository access signs in with permission `none` and sees no
control (the authz table denies everything but nothing reads).

## Implementation

- `console/lib/{auth.config,auth,sessions,github,bff}.ts`,
  `console/app/auth/[...nextauth]/route.ts`, `console/middleware.ts`;
  `next-auth@beta` (Auth.js v5) and `zod` as dependencies; `.env.example`
  gains `AUTH_SECRET`, `AUTH_GITHUB_ID`, `AUTH_GITHUB_SECRET`,
  `CONSOLE_SESSION_KEY`, `CONSOLE_DB_PATH`.
- Tests: `lib/sessions.test.ts`, `lib/github.test.ts`, `lib/bff.test.ts`.

## Runbook (the user, GitHub)

1. On the factory GitHub App enable user authorisation (OAuth) with the
   callback `https://<console-host>/auth/callback/github`; copy the client
   id and generate a client secret.
2. `AUTH_SECRET=$(openssl rand -base64 32)`, `CONSOLE_SESSION_KEY=$(openssl
   rand -base64 32)` into the console's env with the two App values.
3. Sign in as a read-only collaborator and confirm no control renders; sign
   in as a write user and confirm the login pill; sign out and confirm the
   token is gone (`GET /applications/{client_id}/token` answers 404).

## Dependencies

- Units 56 and 54. New dependency: `next-auth@beta`.

## Verify when done

- [ ] The session store round-trips an encrypted token, expires at eight
      hours, deletes on sign-out and sweeps
- [ ] GitHub writes carry the user's bearer token; refusals name the fix;
      revocation uses the App's client credentials
- [ ] The BFF skeleton answers 400, 401, 403 and the standard body in order;
      the governed approval asks the CODEOWNER question
- [ ] `npm run lint`, `typecheck`, `test`, `build` pass; the sign-in flow
      against the real App is the user's runbook
