// The Overview against an orchestrator API seeded with 1,000 tasks (build plan unit 59):
// the page loads under 2 s p95 and the Indicators panel starts collapsed. One untimed request
// warms the server (the p95 is a steady-state figure), then ten timed loads.
import { expect, test } from "@playwright/test";

const RUNS = 10;

test("Overview loads under 2 s p95 with 1,000 seeded tasks and the panel collapsed", async ({ page }) => {
  await page.goto("/", { waitUntil: "domcontentloaded" });
  const timings: number[] = [];
  for (let run = 0; run < RUNS; run += 1) {
    const started = Date.now();
    const response = await page.goto("/", { waitUntil: "domcontentloaded" });
    expect(response?.ok()).toBe(true);
    await expect(page.getByRole("heading", { level: 1, name: "Overview" })).toBeVisible();
    timings.push(Date.now() - started);
  }
  timings.sort((a, b) => a - b);
  const p95 = timings[Math.min(timings.length - 1, Math.ceil(timings.length * 0.95) - 1)] ?? 0;
  console.log(`GATE-EVIDENCE console.overview: ${JSON.stringify({ runs: RUNS, timings_ms: timings, p95_ms: p95 })}`);
  expect(p95).toBeLessThan(2000);

  await expect(page.getByRole("group", { name: /Merge rate/ })).toBeVisible();
  await expect(page.getByRole("heading", { name: /Needs a human/ })).toBeVisible();
  await expect(page.getByRole("heading", { name: /Running and queued/ })).toBeVisible();
  await expect(page.getByRole("meter", { name: "Spend today" })).toBeVisible();
  const panel = page.locator("details", { hasText: "Indicators" });
  await expect(panel).toBeVisible();
  expect(await panel.evaluate((node) => (node as HTMLDetailsElement).open)).toBe(false);
});
