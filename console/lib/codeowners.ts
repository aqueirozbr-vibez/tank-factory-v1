// CODEOWNERS the way the orchestrator reads it (orchestrator/github/codeowners.py; PRD
// Capability 2; TRD §6.2): the last matching rule wins, `@login` matches case-insensitively,
// `@org/team` and e-mail owners never match in v1. Pure functions; the file comes from GitHub
// under the user's token (lib/github.ts).

export type OwnerRule = { pattern: string; owners: string[] };

export const CODEOWNERS_LOCATIONS = ["CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS"] as const;

export function parseCodeowners(text: string): OwnerRule[] {
  const rules: OwnerRule[] = [];
  for (const raw of text.split(/\r?\n/)) {
    const line = (raw.split("#", 1)[0] ?? "").trim();
    if (!line) continue;
    const parts = line.split(/\s+/);
    const [pattern, ...owners] = parts;
    if (pattern) rules.push({ pattern, owners });
  }
  return rules;
}

function escape(ch: string): string {
  return ch.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

export function toRegex(pattern: string): RegExp {
  const anchored = pattern.startsWith("/");
  const body = pattern.replace(/^\/+/, "").replace(/\/+$/, "");
  if (body === "") return /^.*$/;
  const hasSlash = body.includes("/");
  const out: string[] = [];
  let i = 0;
  while (i < body.length) {
    if (body.startsWith("**/", i)) {
      out.push("(?:.*/)?");
      i += 3;
      continue;
    }
    if (body.startsWith("**", i)) {
      out.push(".*");
      i += 2;
      continue;
    }
    const ch = body[i] ?? "";
    if (ch === "*") out.push("[^/]*");
    else if (ch === "?") out.push("[^/]");
    else out.push(escape(ch));
    i += 1;
  }
  const prefix = anchored || hasSlash ? "^" : "^(?:.*/)?";
  return new RegExp(`${prefix}${out.join("")}(?:/.*)?$`);
}

/** The owners of `path`; the last matching rule wins, as GitHub resolves it. */
export function ownersFor(path: string, rules: OwnerRule[]): string[] {
  const normalised = path.replace(/^\/+/, "");
  let winner: string[] = [];
  for (const rule of rules) {
    if (toRegex(rule.pattern).test(normalised)) winner = rule.owners;
  }
  return winner;
}

export function isOwner(login: string | null | undefined, owners: string[]): boolean {
  if (!login) return false;
  const wanted = login.toLowerCase();
  return owners.some((o) => o.startsWith("@") && !o.includes("/") && o.slice(1).toLowerCase() === wanted);
}
