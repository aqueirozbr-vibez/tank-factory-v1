// The console's own session store (TRD §3.4, §4 console.db): one SQLite file the console
// alone writes, one row per session, the user's GitHub token AES-GCM encrypted with a key
// from the console's environment, deleted at expiry or sign-out. Server only.

import { createCipheriv, createDecipheriv, randomBytes } from "node:crypto";
import { DatabaseSync } from "node:sqlite";

import { SESSION_HOURS } from "./session-constants";

export { SESSION_HOURS };
const KEY_BYTES = 32;
const NONCE_BYTES = 12;
const TAG_BYTES = 16;

export type SessionRow = {
  sid: string;
  login: string;
  permission: string;
  created_at: string;
  expires_at: string;
};

export type SessionKey = Buffer;

export function keyFromEnv(value: string | undefined = process.env.CONSOLE_SESSION_KEY): SessionKey {
  if (!value) throw new Error("CONSOLE_SESSION_KEY is not set");
  const key = Buffer.from(value, "base64");
  if (key.length !== KEY_BYTES) {
    throw new Error("CONSOLE_SESSION_KEY must be 32 bytes, base64-encoded");
  }
  return key;
}

export function encryptToken(key: SessionKey, token: string): Buffer {
  const nonce = randomBytes(NONCE_BYTES);
  const cipher = createCipheriv("aes-256-gcm", key, nonce);
  const body = Buffer.concat([cipher.update(token, "utf8"), cipher.final()]);
  return Buffer.concat([nonce, cipher.getAuthTag(), body]);
}

export function decryptToken(key: SessionKey, blob: Buffer): string {
  const nonce = blob.subarray(0, NONCE_BYTES);
  const tag = blob.subarray(NONCE_BYTES, NONCE_BYTES + TAG_BYTES);
  const body = blob.subarray(NONCE_BYTES + TAG_BYTES);
  const decipher = createDecipheriv("aes-256-gcm", key, nonce);
  decipher.setAuthTag(tag);
  return Buffer.concat([decipher.update(body), decipher.final()]).toString("utf8");
}

const SCHEMA = `
CREATE TABLE IF NOT EXISTS sessions (
  sid TEXT PRIMARY KEY,
  login TEXT NOT NULL,
  permission TEXT NOT NULL,
  token_enc BLOB NOT NULL,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_expires ON sessions(expires_at);
`;

export class SessionStore {
  private readonly db: DatabaseSync;
  private readonly key: SessionKey;
  private readonly clock: () => Date;

  constructor(path: string, key: SessionKey, clock: () => Date = () => new Date()) {
    this.db = new DatabaseSync(path);
    this.db.exec("PRAGMA journal_mode = WAL");
    this.db.exec(SCHEMA);
    this.key = key;
    this.clock = clock;
  }

  /** Create a session for a signed-in login; the token leaves this method only encrypted. */
  create(login: string, permission: string, token: string): SessionRow {
    const now = this.clock();
    const expires = new Date(now.getTime() + SESSION_HOURS * 3600 * 1000);
    const sid = randomBytes(24).toString("base64url");
    this.db
      .prepare(
        "INSERT INTO sessions (sid, login, permission, token_enc, created_at, expires_at)" +
          " VALUES (?, ?, ?, ?, ?, ?)",
      )
      .run(sid, login, permission, encryptToken(this.key, token), now.toISOString(), expires.toISOString());
    return { sid, login, permission, created_at: now.toISOString(), expires_at: expires.toISOString() };
  }

  /** The live session, or null when unknown or expired (an expired row is deleted on sight). */
  get(sid: string): SessionRow | null {
    const row = this.db
      .prepare("SELECT sid, login, permission, created_at, expires_at FROM sessions WHERE sid = ?")
      .get(sid) as SessionRow | undefined;
    if (!row) return null;
    if (new Date(row.expires_at).getTime() <= this.clock().getTime()) {
      this.delete(sid);
      return null;
    }
    return row;
  }

  /** The user's GitHub token for a write under their login; never returned to a browser. */
  token(sid: string): string | null {
    if (!this.get(sid)) return null;
    const row = this.db.prepare("SELECT token_enc FROM sessions WHERE sid = ?").get(sid) as
      | { token_enc: Uint8Array }
      | undefined;
    return row ? decryptToken(this.key, Buffer.from(row.token_enc)) : null;
  }

  delete(sid: string): void {
    this.db.prepare("DELETE FROM sessions WHERE sid = ?").run(sid);
  }

  /** Expired rows go at every sweep (TRD §4.4: sessions deleted at expiry). */
  sweep(): number {
    const result = this.db
      .prepare("DELETE FROM sessions WHERE expires_at <= ?")
      .run(this.clock().toISOString());
    return Number(result.changes);
  }

  count(): number {
    const row = this.db.prepare("SELECT COUNT(*) AS n FROM sessions").get() as { n: number };
    return row.n;
  }

  close(): void {
    this.db.close();
  }
}
