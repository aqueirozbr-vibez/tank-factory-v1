// The same cases pytest runs against authz.py, here against the generated authz.ts (TRD §6.2).
import { describe, expect, it } from "vitest";

import cases from "./authz.cases.json";
import { ACTIONS, PERMISSIONS, allowed } from "./authz";

type Case = { permission: string; action: string; codeowner: boolean; allow: boolean };

describe("authz table (generated from authz.yaml)", () => {
  for (const c of cases as Case[]) {
    it(`${c.permission || "(empty)"} ${c.action} codeowner=${c.codeowner} -> ${c.allow}`, () => {
      expect(allowed(c.permission, c.action, c.codeowner)).toBe(c.allow);
    });
  }

  it("denies anything the table does not name", () => {
    expect(allowed("owner", "read")).toBe(false);
    expect(allowed("write", "merge")).toBe(false);
    expect(PERMISSIONS.has("none")).toBe(false);
    expect(ACTIONS.has("read")).toBe(true);
  });
});
