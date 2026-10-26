// Global utility classes the components use must exist in a sheet the layout loads. On
// 2026-10-25 `sr-only` was used by four components and defined nowhere, so screen-reader text
// rendered visibly and spilled over the task detail's phase rows. Vitest does not apply CSS,
// so this reads the sheets as text.
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { PhaseTimeline } from "./PhaseTimeline";

afterEach(cleanup);

const ROOT = process.cwd();
const GLOBAL_SHEETS = ["styles/industry.css", "styles/console-tokens.css"];
const UTILITIES = ["sr-only"];

function globalCss(): string {
  return GLOBAL_SHEETS.map((sheet) => readFileSync(join(ROOT, sheet), "utf-8")).join("\n");
}

function walk(dir: string, keep: (name: string) => boolean): string[] {
  return readdirSync(join(ROOT, dir), { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return walk(path, keep);
    return keep(entry.name) ? [join(ROOT, path)] : [];
  });
}

function moduleSheets(): string[] {
  return [...walk("components", (n) => n.endsWith(".module.css")), ...walk("app", (n) => n.endsWith(".module.css"))];
}

function componentSources(): string[] {
  const source = (n: string) => n.endsWith(".tsx") && !n.endsWith(".test.tsx");
  return [...walk("components", source), ...walk("app", source)];
}

describe("global utility classes", () => {
  it("defines every utility a component uses, sr-only as the visually hidden pattern", () => {
    const css = globalCss();
    const used = readdirSync(join(ROOT, "components"))
      .filter((name) => name.endsWith(".tsx") && !name.endsWith(".test.tsx"))
      .flatMap((name) => {
        const source = readFileSync(join(ROOT, "components", name), "utf-8");
        return UTILITIES.filter((cls) => source.includes(`className="${cls}"`)).map((cls) => ({ cls, name }));
      });
    expect(used.length).toBeGreaterThan(0);
    for (const { cls } of used) {
      expect(css).toMatch(new RegExp(`\\.${cls}\\s*\\{`));
    }
    const rule = /\.sr-only\s*\{([^}]*)\}/.exec(css)?.[1] ?? "";
    expect(rule).toMatch(/position:\s*absolute/);
    expect(rule).toMatch(/width:\s*1px/);
    expect(rule).toMatch(/clip:\s*rect\(0,\s*0,\s*0,\s*0\)/);
    expect(rule).toMatch(/overflow:\s*hidden/);
  });

  it("defines every CSS variable a stylesheet reads (--space-5 was read by four dialogs)", () => {
    const globals = new Set([...globalCss().matchAll(/(--[\w-]+)\s*:/g)].map((m) => m[1]));
    const missing: string[] = [];
    for (const file of moduleSheets()) {
      const css = readFileSync(file, "utf-8");
      const local = new Set([...css.matchAll(/(--[\w-]+)\s*:/g)].map((m) => m[1]));
      for (const [, name] of css.matchAll(/var\((--[\w-]+)/g)) {
        if (name && !globals.has(name) && !local.has(name)) missing.push(`${file}: ${name}`);
      }
    }
    expect(missing).toEqual([]);
  });

  it("defines every module class a component uses (the Overview's `controls` was missing)", () => {
    const missing: string[] = [];
    for (const file of componentSources()) {
      const source = readFileSync(file, "utf-8");
      for (const [, name, rel] of source.matchAll(/import\s+(\w+)\s+from\s+"(\.\/[^"]+\.module\.css)"/g)) {
        if (!name || !rel) continue;
        const css = readFileSync(join(file, "..", rel), "utf-8");
        const defined = new Set([...css.matchAll(/\.([A-Za-z_][\w-]*)/g)].map((m) => m[1]));
        for (const [, cls] of source.matchAll(new RegExp(`\\b${name}\\.([A-Za-z_]\\w*)`, "g"))) {
          if (cls && !defined.has(cls)) missing.push(`${file}: ${name}.${cls}`);
        }
      }
    }
    expect([...new Set(missing)]).toEqual([]);
  });

  it("never takes a table cell out of table layout (a flex <td> detached its row's border)", () => {
    const broken: string[] = [];
    for (const file of componentSources()) {
      const source = readFileSync(file, "utf-8");
      const imported = /import\s+(\w+)\s+from\s+"(\.\/[^"]+\.module\.css)"/.exec(source);
      if (!imported?.[1] || !imported[2]) continue;
      const css = readFileSync(join(file, "..", imported[2]), "utf-8");
      const cells = new RegExp(`<t[dh]\\b[^>]*className=\\{?[\`"]?[^>]*?\\b${imported[1]}\\.(\\w+)`, "g");
      for (const [, cls] of source.matchAll(cells)) {
        const rule = new RegExp(`\\.${cls}\\s*\\{([^}]*)\\}`).exec(css)?.[1] ?? "";
        if (/display:\s*(flex|grid|block|inline-flex)/.test(rule)) broken.push(`${file}: ${cls}`);
      }
    }
    expect(broken).toEqual([]);
  });

  it("puts dialogs on the surface over the backdrop, after industry.css's transparent frame rule", () => {
    const tokens = readFileSync(join(ROOT, "styles/console-tokens.css"), "utf-8");
    const dialog = [...tokens.matchAll(/(^|\n)\.dialog\s*\{([^}]*)\}/g)].map((m) => m[2] ?? "").join("\n");
    expect(dialog).toMatch(/background:\s*var\(--color-surface\)/);
    expect(dialog).toMatch(/box-shadow:\s*var\(--shadow-lg\)/);
    const backdrop = /\.dialog-backdrop\s*\{([^}]*)\}/.exec(tokens)?.[1] ?? "";
    expect(backdrop).toMatch(/z-index:\s*\d+/);
    const layout = readFileSync(join(ROOT, "app/layout.tsx"), "utf-8");
    expect(layout.indexOf("console-tokens.css")).toBeGreaterThan(layout.indexOf("industry.css"));
  });

  it("keeps a phase's meta on one line with the full text in its title", () => {
    const css = readFileSync(join(ROOT, "components/PhaseTimeline.module.css"), "utf-8");
    const meta = /\.meta\s*\{([^}]*)\}/.exec(css)?.[1] ?? "";
    expect(meta).toMatch(/white-space:\s*nowrap/);
    expect(meta).toMatch(/text-overflow:\s*ellipsis/);
    render(
      <PhaseTimeline
        now={new Date("2026-10-25T20:30:00Z")}
        phases={[
          {
            phase_id: "p1",
            run_id: "r1",
            seq: 1,
            name: "build",
            kind: "agent",
            owner: "builder",
            status: "pass",
            attempt: 1,
            retries: 0,
            harness: "claude_code",
            harness_version: "2.1.310",
            model_id: "claude-opus-5-5",
            session_id: null,
            timeout_s: 7200,
            phase_cap_usd: 3,
            tokens_in: 30,
            tokens_out: 16356,
            tokens_cache_read: 0,
            tokens_cache_write: 0,
            cost_usd: 0.49,
            started_at: "2026-10-25T20:27:23Z",
            ended_at: "2026-10-25T20:29:40Z",
            error: null,
          },
        ]}
      />,
    );
    const text = "2 min 17 s · $0.49 · claude_code claude-opus-5-5";
    expect(screen.getByTitle(text).textContent).toBe(text);
  });
});
