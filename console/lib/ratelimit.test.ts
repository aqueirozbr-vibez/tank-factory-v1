import { afterEach, describe, expect, it } from "vitest";

import { CONFIG_PR_RULE, LABELS_RULE, allow, resetLimits } from "./ratelimit";

afterEach(resetLimits);

describe("the console's per-login write limits", () => {
  it("allows 30 label writes a minute per login, then refuses until the window turns", () => {
    const t0 = 1_000_000;
    for (let n = 0; n < 30; n += 1) expect(allow("rsouza", LABELS_RULE, t0 + n)).toBe(true);
    expect(allow("rsouza", LABELS_RULE, t0 + 31)).toBe(false);
    expect(allow("mlima", LABELS_RULE, t0 + 31)).toBe(true); // another login, another window
    expect(allow("rsouza", LABELS_RULE, t0 + 60_000)).toBe(true);
  });

  it("keeps the rules apart", () => {
    for (let n = 0; n < 10; n += 1) expect(allow("rsouza", CONFIG_PR_RULE, n)).toBe(true);
    expect(allow("rsouza", CONFIG_PR_RULE, 11)).toBe(false);
    expect(allow("rsouza", LABELS_RULE, 11)).toBe(true);
    expect(CONFIG_PR_RULE.name).toBe("config PRs: 10 per day per login");
  });
});
