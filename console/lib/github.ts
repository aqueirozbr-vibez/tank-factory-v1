// GitHub under the signed-in user's token (TRD §6.1 BFF rows, §6.2): labels on an issue, the
// CODEOWNERS file, the `.factory/` files at main, open `factory-config/*` pull requests with
// their diffs, the branch-commit-PR flow a proposed change takes, and the token's revocation
// at sign-out. Nothing else; the orchestrator owns every other GitHub write under the App.

import { CODEOWNERS_LOCATIONS } from "./codeowners";

export const GITHUB_API = "https://api.github.com";

export type GitHubFetch = typeof fetch;

export class GitHubRefused extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

function headers(token: string): Record<string, string> {
  return {
    accept: "application/vnd.github+json",
    authorization: `Bearer ${token}`,
    "x-github-api-version": "2022-11-28",
  };
}

async function refused(response: Response, what: string, repo: string): Promise<GitHubRefused> {
  const hint =
    response.status === 403
      ? `your token lacks write access on ${repo}`
      : response.status === 404
        ? `the issue or label was not found on ${repo}`
        : response.status === 422
          ? "GitHub rejected the request (422)"
          : `GitHub answered ${response.status}`;
  return new GitHubRefused(response.status, `GitHub refused ${what} (${response.status}): ${hint}`);
}

export type Repo = { owner: string; repo: string };

function repoPath(target: Repo): string {
  return `${GITHUB_API}/repos/${target.owner}/${target.repo}`;
}

export async function addLabel(
  token: string,
  target: Repo,
  issue: number,
  label: string,
  fetchImpl: GitHubFetch = fetch,
): Promise<string[]> {
  const url = `${repoPath(target)}/issues/${issue}/labels`;
  const response = await fetchImpl(url, {
    method: "POST",
    headers: { ...headers(token), "content-type": "application/json" },
    body: JSON.stringify({ labels: [label] }),
  });
  if (!response.ok) throw await refused(response, "the label write", `${target.owner}/${target.repo}`);
  const labels = (await response.json()) as Array<{ name: string }>;
  return labels.map((l) => l.name);
}

export async function removeLabel(
  token: string,
  target: Repo,
  issue: number,
  label: string,
  fetchImpl: GitHubFetch = fetch,
): Promise<void> {
  const url = `${repoPath(target)}/issues/${issue}/labels/` + encodeURIComponent(label);
  const response = await fetchImpl(url, { method: "DELETE", headers: headers(token) });
  if (!response.ok && response.status !== 404) {
    throw await refused(response, "the label removal", `${target.owner}/${target.repo}`);
  }
}

/** One file's raw text at a ref through the Contents API; null when it does not exist. */
export async function fileAtRef(
  token: string,
  target: Repo,
  path: string,
  ref = "main",
  fetchImpl: GitHubFetch = fetch,
): Promise<string | null> {
  const url = `${repoPath(target)}/contents/${path}?ref=${encodeURIComponent(ref)}`;
  const response = await fetchImpl(url, {
    headers: { ...headers(token), accept: "application/vnd.github.raw+json" },
  });
  if (response.status === 404) return null;
  if (!response.ok) throw await refused(response, `the read of ${path}`, `${target.owner}/${target.repo}`);
  return response.text();
}

/** The CODEOWNERS file at main in GitHub's own lookup order, or null when the repo has none. */
export async function codeownersText(token: string, target: Repo, fetchImpl: GitHubFetch = fetch): Promise<string | null> {
  for (const location of CODEOWNERS_LOCATIONS) {
    const text = await fileAtRef(token, target, location, "main", fetchImpl);
    if (text !== null) return text;
  }
  return null;
}

export type ContentsFile = { sha: string; text: string };

/** A file with its blob sha (the Contents API's JSON form), for a later write. */
export async function contentsAt(
  token: string,
  target: Repo,
  path: string,
  ref: string,
  fetchImpl: GitHubFetch = fetch,
): Promise<ContentsFile | null> {
  const url = `${repoPath(target)}/contents/${path}?ref=${encodeURIComponent(ref)}`;
  const response = await fetchImpl(url, { headers: headers(token) });
  if (response.status === 404) return null;
  if (!response.ok) throw await refused(response, `the read of ${path}`, `${target.owner}/${target.repo}`);
  const body = (await response.json()) as { sha: string; content?: string; encoding?: string };
  const text = body.content ? Buffer.from(body.content.replace(/\n/g, ""), "base64").toString("utf8") : "";
  return { sha: body.sha, text };
}

export async function refSha(token: string, target: Repo, branch: string, fetchImpl: GitHubFetch = fetch): Promise<string> {
  const response = await fetchImpl(`${repoPath(target)}/git/ref/heads/${encodeURIComponent(branch)}`, { headers: headers(token) });
  if (!response.ok) throw await refused(response, `the read of branch ${branch}`, `${target.owner}/${target.repo}`);
  const body = (await response.json()) as { object: { sha: string } };
  return body.object.sha;
}

/** A branch from `sha`; GitHub answers 422 when it exists, which the caller passes through. */
export async function createBranch(token: string, target: Repo, branch: string, sha: string, fetchImpl: GitHubFetch = fetch): Promise<void> {
  const response = await fetchImpl(`${repoPath(target)}/git/refs`, {
    method: "POST",
    headers: { ...headers(token), "content-type": "application/json" },
    body: JSON.stringify({ ref: `refs/heads/${branch}`, sha }),
  });
  if (!response.ok) {
    if (response.status === 422) throw new GitHubRefused(422, `branch ${branch} already exists`);
    throw await refused(response, `the branch ${branch}`, `${target.owner}/${target.repo}`);
  }
}

export async function putContents(
  token: string,
  target: Repo,
  path: string,
  input: { branch: string; message: string; text: string; sha: string | null },
  fetchImpl: GitHubFetch = fetch,
): Promise<string> {
  const body: Record<string, string> = {
    message: input.message,
    content: Buffer.from(input.text, "utf8").toString("base64"),
    branch: input.branch,
  };
  if (input.sha) body.sha = input.sha;
  const response = await fetchImpl(`${repoPath(target)}/contents/${path}`, {
    method: "PUT",
    headers: { ...headers(token), "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw await refused(response, `the commit of ${path}`, `${target.owner}/${target.repo}`);
  const result = (await response.json()) as { commit: { sha: string } };
  return result.commit.sha;
}

export type PullRequest = { number: number; html_url: string; title: string; body: string | null; head: string; user: string; created_at: string };

export async function openPullRequest(
  token: string,
  target: Repo,
  input: { title: string; body: string; head: string; base: string },
  fetchImpl: GitHubFetch = fetch,
): Promise<PullRequest> {
  const response = await fetchImpl(`${repoPath(target)}/pulls`, {
    method: "POST",
    headers: { ...headers(token), "content-type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!response.ok) throw await refused(response, "the pull request", `${target.owner}/${target.repo}`);
  return pullView((await response.json()) as RawPull);
}

type RawPull = {
  number: number;
  html_url: string;
  title: string;
  body: string | null;
  head: { ref: string };
  user: { login: string } | null;
  created_at: string;
};

function pullView(raw: RawPull): PullRequest {
  return {
    number: raw.number,
    html_url: raw.html_url,
    title: raw.title,
    body: raw.body,
    head: raw.head.ref,
    user: raw.user?.login ?? "",
    created_at: raw.created_at,
  };
}

/** Open pull requests whose head branch starts with `prefix` (the console's `.factory/` changes). */
export async function openPulls(token: string, target: Repo, prefix: string, fetchImpl: GitHubFetch = fetch): Promise<PullRequest[]> {
  const response = await fetchImpl(`${repoPath(target)}/pulls?state=open&per_page=50`, { headers: headers(token) });
  if (!response.ok) throw await refused(response, "the pull request list", `${target.owner}/${target.repo}`);
  const raw = (await response.json()) as RawPull[];
  return raw.filter((p) => p.head.ref.startsWith(prefix)).map(pullView);
}

export type PullFile = { filename: string; status: string; patch: string | null };

export async function pullFiles(token: string, target: Repo, number: number, fetchImpl: GitHubFetch = fetch): Promise<PullFile[]> {
  const response = await fetchImpl(`${repoPath(target)}/pulls/${number}/files?per_page=50`, { headers: headers(token) });
  if (!response.ok) throw await refused(response, `the files of #${number}`, `${target.owner}/${target.repo}`);
  const raw = (await response.json()) as Array<{ filename: string; status: string; patch?: string }>;
  return raw.map((f) => ({ filename: f.filename, status: f.status, patch: f.patch ?? null }));
}

/** The signed-in user's repository permission, as GitHub sees it for the signed-in login. */
export async function viewerPermission(
  token: string,
  target: Repo,
  login: string,
  fetchImpl: GitHubFetch = fetch,
): Promise<string> {
  const url = `${repoPath(target)}/collaborators/${login}/permission`;
  const response = await fetchImpl(url, { headers: headers(token) });
  if (!response.ok) return "none";
  const body = (await response.json()) as { permission?: string };
  return body.permission ?? "none";
}

/** Sign-out revokes the user-to-server token at GitHub (TRD §4.4): the App's client credentials. */
export async function revokeToken(
  clientId: string,
  clientSecret: string,
  token: string,
  fetchImpl: GitHubFetch = fetch,
): Promise<boolean> {
  const basic = Buffer.from(`${clientId}:${clientSecret}`).toString("base64");
  const response = await fetchImpl(`${GITHUB_API}/applications/${clientId}/token`, {
    method: "DELETE",
    headers: {
      accept: "application/vnd.github+json",
      authorization: `Basic ${basic}`,
      "content-type": "application/json",
    },
    body: JSON.stringify({ access_token: token }),
  });
  return response.status === 204;
}
