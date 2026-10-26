// The runtime controls against the seeded orchestrator API (build plan unit 65): a budget raise
// and the concurrency and admissions controls each show as an events row with the login; the
// kill-switch from the console stops the factory within 60 s and the running task is cancelled.
// Runs last in the e2e set: the kill-switch cancels the seeded running task.
import { expect, test } from "@playwright/test";

const API = process.env.ORCHESTRATOR_URL ?? "http://127.0.0.1:8000";
const TASK = "acme/atlas-api#7";
const LOGIN = process.env.E2E_LOGIN ?? "mlima";

test.describe.configure({ mode: "serial" });

test("a budget raise from the task detail is one control event with the login", async ({ page, request }) => {
  await page.goto(`/tasks/${encodeURIComponent(TASK)}`, { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: "Raise budget…" }).click();
  await page.getByLabel("Reason").fill("e2e: long suite");
  await page.getByRole("button", { name: "Raise budget", exact: true }).click();
  const status = page.getByRole("status").filter({ hasText: /Budget raised/ });
  await expect(status).toContainText(`with @${LOGIN}`);
  const events = await (await request.get(`${API}/api/v1/tasks/${encodeURIComponent(TASK)}/events?kind=control`)).json();
  const raise = (events.items as Array<{ actor: string; payload: { action?: string; old?: number; new?: number } }>).find((e) => e.payload?.action === "budget_raise");
  expect(raise?.actor).toBe(LOGIN);
  expect(raise?.payload.new).toBe(12);
  await page.keyboard.press("Escape");
});

test("concurrency and admissions controls record events rows with the login", async ({ page }) => {
  await page.goto("/", { waitUntil: "domcontentloaded" });
  const concurrency = page.getByRole("form", { name: "Concurrency" });
  await concurrency.getByLabel("New N").fill("4");
  await concurrency.getByPlaceholder("Reason (required)").fill("e2e: capacity");
  await concurrency.getByRole("button", { name: "Set N" }).click();
  await expect(concurrency.getByRole("status")).toContainText(new RegExp(`N \\d+ → 4 · events row #\\d+ with @${LOGIN}`));

  const admissions = page.getByRole("form", { name: "Admissions" });
  await admissions.getByPlaceholder("Reason (required)").fill("e2e: pause");
  await admissions.getByRole("button", { name: "Pause admissions" }).click();
  await expect(admissions.getByRole("status")).toContainText(new RegExp(`admissions paused · events row #\\d+ with @${LOGIN}`));
  await page.reload({ waitUntil: "domcontentloaded" });
  const again = page.getByRole("form", { name: "Admissions" });
  await again.getByPlaceholder("Reason (required)").fill("e2e: resume");
  await again.getByRole("button", { name: "Resume admissions" }).click();
  await expect(again.getByRole("status")).toContainText(/admissions resumed/);
});

test("the kill-switch stops the factory within 60 s, cancels the running task and records the login", async ({ page, request }) => {
  test.setTimeout(180_000); // the restart waits out the orchestrator's one-per-minute kill-switch limit
  await page.goto("/", { waitUntil: "domcontentloaded" });
  const pill = page.getByTestId("kill-switch-pill");
  await expect(pill).toContainText("FACTORY_ENABLED=1");
  await page.getByRole("button", { name: "Stop factory…" }).click();
  const dialog = page.getByRole("dialog");
  const reason = dialog.getByLabel("Reason");
  await expect(reason).toBeFocused();
  const confirm = dialog.getByRole("button", { name: "Stop factory", exact: true });
  await expect(confirm).toBeDisabled();
  await reason.fill("e2e: stop drill");
  const started = Date.now();
  await reason.press("Enter");
  const status = dialog.getByTestId("kill-switch-result");
  await expect(status).toContainText(new RegExp(`Recorded as events row #\\d+ with your login @${LOGIN}`), { timeout: 70_000 });
  const elapsed = Date.now() - started;
  const health = await (await request.get(`${API}/healthz`)).json();
  const task = await (await request.get(`${API}/api/v1/tasks/${encodeURIComponent(TASK)}`)).json();
  console.log(`GATE-EVIDENCE console.kill_switch: ${JSON.stringify({ stop_ms: elapsed, factory_enabled: health.factory_enabled, live_after: health.live_processes, task_status: task.task.status })}`);
  expect(health.factory_enabled).toBe(false);
  expect(task.task.status).toBe("cancelled");
  expect(elapsed).toBeLessThan(60_000);

  await page.keyboard.press("Escape");
  await page.waitForTimeout(61_000); // TRD §6.1: the kill-switch is limited to 1 a minute per login
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(pill).toContainText("FACTORY_ENABLED=0");
  await page.getByRole("button", { name: "Start factory…" }).click();
  const restart = page.getByRole("dialog");
  await restart.getByLabel("Reason").fill("e2e: start again");
  await restart.getByRole("button", { name: "Start factory", exact: true }).click();
  await expect(restart.getByTestId("kill-switch-result")).toContainText("Factory on");
  const after = await (await request.get(`${API}/healthz`)).json();
  expect(after.factory_enabled).toBe(true);
});
