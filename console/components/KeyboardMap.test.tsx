import { describe, expect, it } from "vitest";

import { MAP, SCREEN_KEYS, isTypingTarget } from "./KeyboardMap";

describe("KeyboardMap", () => {
  it("maps 1 to 6 onto the six screens in the UI context's order", () => {
    expect(Object.entries(SCREEN_KEYS)).toEqual([
      ["1", "/"],
      ["2", "/tasks"],
      ["3", "/tasks/review"],
      ["4", "/approvals"],
      ["5", "/eval"],
      ["6", "/config"],
    ]);
    expect(MAP.map(([keys]) => keys)).toContain("?");
  });

  it("leaves typing targets alone", () => {
    const input = document.createElement("input");
    const div = document.createElement("div");
    expect(isTypingTarget(input)).toBe(true);
    expect(isTypingTarget(div)).toBe(false);
    expect(isTypingTarget(null)).toBe(false);
  });
});
