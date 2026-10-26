import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import { SESSION_HOURS, SessionStore, decryptToken, encryptToken, keyFromEnv } from "./sessions";

const KEY = keyFromEnv(Buffer.alloc(32, 7).toString("base64"));
const stores: SessionStore[] = [];

function store(clock: () => Date): SessionStore {
  const dir = mkdtempSync(join(tmpdir(), "console-sessions-"));
  const s = new SessionStore(join(dir, "console.db"), KEY, clock);
  stores.push(s);
  return s;
}

afterEach(() => {
  for (const s of stores.splice(0)) s.close();
});

describe("session key", () => {
  it("needs 32 base64 bytes", () => {
    expect(() => keyFromEnv(undefined)).toThrow("CONSOLE_SESSION_KEY");
    expect(() => keyFromEnv("short")).toThrow("32 bytes");
    expect(KEY.length).toBe(32);
  });
});

describe("token encryption", () => {
  it("round-trips and never stores the clear token", () => {
    const blob = encryptToken(KEY, "gho_secret");
    expect(blob.toString("latin1")).not.toContain("gho_secret");
    expect(decryptToken(KEY, blob)).toBe("gho_secret");
    const other = keyFromEnv(Buffer.alloc(32, 9).toString("base64"));
    expect(() => decryptToken(other, blob)).toThrow();
  });
});

describe("SessionStore", () => {
  it("creates an 8-hour session, serves the token server-side and deletes on expiry", () => {
    let now = new Date("2026-10-24T12:00:00Z");
    const s = store(() => now);
    const row = s.create("rsouza", "write", "gho_user");
    expect(row.login).toBe("rsouza");
    expect(new Date(row.expires_at).getTime() - new Date(row.created_at).getTime()).toBe(
      SESSION_HOURS * 3600 * 1000,
    );
    expect(s.get(row.sid)?.permission).toBe("write");
    expect(s.token(row.sid)).toBe("gho_user");
    expect(JSON.stringify(s.get(row.sid))).not.toContain("gho_user");
    now = new Date("2026-10-24T20:00:01Z");
    expect(s.get(row.sid)).toBeNull();
    expect(s.token(row.sid)).toBeNull();
    expect(s.count()).toBe(0);
  });

  it("deletes on sign-out and sweeps expired rows", () => {
    let now = new Date("2026-10-24T12:00:00Z");
    const s = store(() => now);
    const a = s.create("rsouza", "write", "t1");
    s.create("mlima", "admin", "t2");
    s.delete(a.sid);
    expect(s.count()).toBe(1);
    now = new Date("2026-10-25T12:00:00Z");
    expect(s.sweep()).toBe(1);
    expect(s.count()).toBe(0);
  });
});
