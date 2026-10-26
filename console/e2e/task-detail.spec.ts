// The Task detail against the seeded orchestrator API (build plan unit 60): the task with
// 10,000 trace rows paints under 2 s p95; a new row appears within 5 s; `p` pauses the tail;
// gates after a failure are not clickable; the earlier pages load to the start of the trace.
import { expect, test } from "@playwright/test";

const RUNS = 10;
const TASK = "acme/atlas-api#7"; // tests/e2e/serve_seeded_api.py seeds it with 10,000 rows
const PATH = `/tasks/${encodeURIComponent(TASK)}`;

test("Task detail paints under 2 s p95 with 10,000 trace rows", async ({ page }) => {
  await page.goto(PATH, { waitUntil: "domcontentloaded" }); // warm-up; the p95 is steady state
  const timings: number[] = [];
  for (let run = 0; run < RUNS; run += 1) {
    const started = Date.now();
    const response = await page.goto(PATH, { waitUntil: "domcontentloaded" });
    expect(response?.ok()).toBe(true);
    await expect(page.getByRole("heading", { level: 1, name: "Task detail" })).toBeVisible();
    await expect(page.getByTestId("trace-table").locator("tbody tr").first()).toBeVisible();
    timings.push(Date.now() - started);
  }
  timings.sort((a, b) => a - b);
  const p95 = timings[Math.min(timings.length - 1, Math.ceil(timings.length * 0.95) - 1)] ?? 0;
  console.log(`GATE-EVIDENCE console.task_detail: ${JSON.stringify({ runs: RUNS, timings_ms: timings, p95_ms: p95 })}`);
  expect(p95).toBeLessThan(2000);
  await expect(page.getByRole("img", { name: /Task state: running/ })).toBeVisible();
  await expect(page.getByRole("img", { name: /Risk: risk:high/ }).first()).toBeVisible();
  await expect(page.getByRole("meter", { name: "Task budget" })).toBeVisible();
  await expect(page.getByRole("status").filter({ hasText: /chain verified/ })).toBeVisible();
});

test("a new row appears within 5 s, p pauses the tail, and the paused tail keeps its rows", async ({ page }) => {
  await page.goto(PATH, { waitUntil: "domcontentloaded" });
  const header = page.getByTestId("trace-header");
  await expect(header).toContainText(/live|polling/, { timeout: 15_000 });
  const first = page.getByTestId("trace-table").locator("tbody tr").first();
  const before = Number(await first.getAttribute("data-seq"));
  const started = Date.now();
  await expect
    .poll(async () => Number(await first.getAttribute("data-seq")), { timeout: 5000, intervals: [100, 250, 500] })
    .toBeGreaterThan(before);
  const arrivedMs = Date.now() - started;
  console.log(`GATE-EVIDENCE console.live_tail: ${JSON.stringify({ new_row_within_ms: arrivedMs })}`);
  expect(arrivedMs).toBeLessThan(5000);

  await page.locator("body").press("p");
  await expect(page.getByRole("button", { name: "Resume tail" })).toBeVisible();
  await expect(header).toContainText("paused");
  const frozen = Number(await first.getAttribute("data-seq"));
  await page.waitForTimeout(4500);
  expect(Number(await first.getAttribute("data-seq"))).toBe(frozen);
  await page.locator("body").press("p");
  await expect(page.getByRole("button", { name: "Pause tail" })).toBeVisible();
  await expect
    .poll(async () => Number(await first.getAttribute("data-seq")), { timeout: 5000 })
    .toBeGreaterThan(frozen);
});

test("gates after a failure read skipped and are not clickable; the first failure's evidence is open", async ({ page }) => {
  await page.goto(PATH, { waitUntil: "domcontentloaded" });
  const ladder = page.getByRole("list", { name: "Gates as run" });
  await expect(ladder).toBeVisible();
  const items = ladder.getByRole("listitem");
  const texts = await items.allTextContents();
  const lintIndex = texts.findIndex((t) => t.includes("lint") && t.includes("fail"));
  expect(lintIndex).toBeGreaterThan(0);
  const skipped = items.filter({ hasText: "– skipped" });
  expect(await skipped.count()).toBeGreaterThanOrEqual(2);
  for (const row of await skipped.all()) {
    expect(await row.getByRole("button").count()).toBe(0);
  }
  const drawer = page.getByRole("complementary", { name: /Evidence for lint/ });
  await expect(drawer).toBeVisible();
  await expect(drawer).toContainText("uv run ruff check .");
  await expect(drawer).toContainText("F841");
  await page.keyboard.press("Escape");
  await expect(drawer).toBeHidden();
});

test("earlier pages load through the console until the start of the trace", async ({ page }) => {
  await page.goto(PATH, { waitUntil: "domcontentloaded" });
  const table = page.getByTestId("trace-table");
  await expect(table.locator("tbody tr").first()).toBeVisible();
  const started = Date.now();
  for (let round = 0; round < 80; round += 1) {
    const button = page.getByRole("button", { name: /Earlier rows|Loading earlier rows/ });
    if ((await button.count()) === 0) break;
    await button.first().click();
    await page.waitForTimeout(50);
  }
  await expect(page.getByText("start of the trace")).toBeVisible({ timeout: 30_000 });
  const walked = Date.now() - started;
  const header = await page.getByTestId("trace-header").textContent();
  const rows = Number(/([\d,]+) rows/.exec(header ?? "")?.[1]?.replace(/,/g, "") ?? 0);
  console.log(`GATE-EVIDENCE console.trace_pages: ${JSON.stringify({ rows_loaded: rows, walk_ms: walked })}`);
  expect(rows).toBeGreaterThanOrEqual(10_000);
});
