import { describe, expect, it } from "vitest";

import { absoluteStamp } from "./Freshness";

describe("Freshness", () => {
  it("prints the absolute timestamp with its offset", () => {
    const at = new Date(2026, 9, 23, 14, 28, 7);
    const stamp = absoluteStamp(at);
    expect(stamp.startsWith("2026-10-23 14:28:07 ")).toBe(true);
    expect(stamp).toMatch(/ [+-]\d{2}$/);
  });
});
