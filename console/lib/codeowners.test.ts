import { describe, expect, it } from "vitest";

import { isOwner, ownersFor, parseCodeowners, toRegex } from "./codeowners";

const RULES = parseCodeowners(`
# the default owner first, GitHub style
*                 @mlima
*.py              @rsouza
/docs/            @docs-team @org/writers
src/billing/**    @cfo
tests/acceptance/ @qa   # the holdout
`);

describe("CODEOWNERS as the orchestrator reads it", () => {
  it("lets the last matching rule win", () => {
    expect(ownersFor("src/billing/invoice.py", RULES)).toEqual(["@cfo"]);
    expect(ownersFor("src/api/app.py", RULES)).toEqual(["@rsouza"]);
    expect(ownersFor("README.md", RULES)).toEqual(["@mlima"]);
    expect(ownersFor("docs/adr/0001.md", RULES)).toEqual(["@docs-team", "@org/writers"]);
    expect(ownersFor("tests/acceptance/test_invoice.py", RULES)).toEqual(["@qa"]);
    expect(ownersFor("/src/billing/x.py", RULES)).toEqual(["@cfo"]);
    expect(ownersFor("anything", [])).toEqual([]);
  });

  it("matches @login case-insensitively and never a team or an e-mail", () => {
    expect(isOwner("RSouza", ["@rsouza"])).toBe(true);
    expect(isOwner("rsouza", ["@org/writers"])).toBe(false);
    expect(isOwner("rsouza", ["rsouza@example.com"])).toBe(false);
    expect(isOwner(null, ["@rsouza"])).toBe(false);
    expect(isOwner("", ["@rsouza"])).toBe(false);
  });

  it("compiles the patterns the way the Python matcher does", () => {
    expect(toRegex("*.py").test("a/b/c.py")).toBe(true);
    expect(toRegex("*.py").test("a/b/c.pyc")).toBe(false);
    expect(toRegex("/docs/").test("docs/x.md")).toBe(true);
    expect(toRegex("/docs/").test("src/docs/x.md")).toBe(false);
    expect(toRegex("docs/").test("src/docs/x.md")).toBe(true); // a trailing slash alone does not anchor
    expect(toRegex("**/logs").test("a/logs/x")).toBe(true);
    expect(toRegex("build").test("src/build/out.js")).toBe(true);
    expect(toRegex("*").test("any/thing")).toBe(true);
    expect(toRegex("a?c").test("abc")).toBe(true);
    expect(toRegex("a?c").test("a/c")).toBe(false);
  });
});
