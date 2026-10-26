// The six screens' primary views in light and dark with axe-core (TRD §12 End to end: WCAG 2.2
// AA, 0 critical, 0 serious), and the keyboard map (ui-context.md · Layout Patterns): `?` shows
// it, 1 to 6 go to the screens, `j`/`k` move between rows, Enter opens the focused row, `Esc`
// closes. Build plan unit 66.
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

const TASK = encodeURIComponent("acme/atlas-api#7");
const SCREENS = [
  { slug: "overview", path: "/", heading: "Overview" },
  { slug: "task_detail", path: `/tasks/${TASK}`, heading: "Task detail" },
  { slug: "review", path: `/tasks/${TASK}/review`, heading: "Review findings" },
  { slug: "approvals_plans", path: "/approvals?tab=plans", heading: "Approvals" },
  { slug: "approvals_audit", path: "/approvals?tab=audit", heading: "Approvals" },
  { slug: "eval", path: "/eval", heading: "Eval and versions" },
  { slug: "config_roster", path: "/config?tab=roster", heading: "Configuration" },
  { slug: "config_instruments", path: "/config?tab=instruments", heading: "Configuration" },
] as const;

async function audit(page: Page, scheme: "light" | "dark", screen: (typeof SCREENS)[number]) {
  await page.emulateMedia({ colorScheme: scheme });
  await page.goto(screen.path, { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("heading", { level: 1, name: screen.heading })).toBeVisible();
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
  const counts = { critical: 0, serious: 0, moderate: 0, minor: 0 } as Record<string, number>;
  for (const violation of results.violations) counts[violation.impact ?? "minor"] = (counts[violation.impact ?? "minor"] ?? 0) + 1;
  console.log(`GATE-EVIDENCE console.axe.${scheme}.${screen.slug}: ${JSON.stringify({ ...counts, rules: results.violations.map((v) => v.id) })}`);
  const blocking = results.violations.filter((v) => v.impact === "critical" || v.impact === "serious");
  expect(blocking.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).slice(0, 3).join(" | ")}`)).toEqual([]);
}

for (const scheme of ["light", "dark"] as const) {
  for (const screen of SCREENS) {
    test(`${screen.heading} (${screen.slug}) in ${scheme} has no critical or serious axe findings`, async ({ page }) => {
      await audit(page, scheme, screen);
    });
  }
}

test("the keyboard map: ? shows it, 1 to 6 navigate, j/k move between rows, Enter opens, Esc closes", async ({ page }) => {
  await page.goto("/", { waitUntil: "domcontentloaded" });
  await page.locator("body").press("?");
  const map = page.getByRole("dialog", { name: "Keyboard map" });
  await expect(map).toBeVisible();
  await expect(map).toContainText("move between rows");
  await page.keyboard.press("Escape");
  await expect(map).toBeHidden();

  await page.locator("body").press("4");
  await expect(page).toHaveURL(/\/approvals/);
  await page.locator("body").press("1");
  await expect(page.getByRole("heading", { level: 1, name: "Overview" })).toBeVisible();

  await page.locator("body").press("j");
  const first = page.locator("[data-row]:focus");
  await expect(first).toHaveCount(1);
  await page.keyboard.press("j");
  const second = await page.evaluate(() => {
    const rows = Array.from(document.querySelectorAll("[data-row]"));
    return rows.indexOf(document.activeElement as Element);
  });
  expect(second).toBe(1);
  await page.keyboard.press("k");
  const back = await page.evaluate(() => Array.from(document.querySelectorAll("[data-row]")).indexOf(document.activeElement as Element));
  expect(back).toBe(0);
  const target = await page.evaluate(() => {
    const row = document.activeElement as HTMLElement | null;
    return row?.querySelector("a[href]")?.getAttribute("href") ?? null;
  });
  expect(target).not.toBeNull();
  await page.keyboard.press("Enter");
  await page.waitForURL((url) => url.pathname + url.search === target);
});
