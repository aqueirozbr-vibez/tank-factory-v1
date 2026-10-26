import { describe, expect, it } from "vitest";

import { GitHubRefused, addLabel, removeLabel, revokeToken, viewerPermission } from "./github";

type Call = { url: string; init: RequestInit | undefined };

function fakeFetch(responder: (call: Call) => Response): { calls: Call[]; fetch: typeof fetch } {
  const calls: Call[] = [];
  const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const call = { url: String(input), init };
    calls.push(call);
    return responder(call);
  }) as typeof fetch;
  return { calls, fetch: impl };
}

const REPO = { owner: "acme", repo: "atlas-api" };

describe("GitHub writes under the user's token", () => {
  it("adds a label with the user's bearer token and returns the label set", async () => {
    const gh = fakeFetch(() => Response.json([{ name: "factory:plan-approved" }, { name: "factory:ready" }]));
    const labels = await addLabel("gho_user", REPO, 418, "factory:plan-approved", gh.fetch);
    expect(labels).toEqual(["factory:plan-approved", "factory:ready"]);
    const call = gh.calls[0];
    expect(call?.url).toBe("https://api.github.com/repos/acme/atlas-api/issues/418/labels");
    const headers = call?.init?.headers as Record<string, string>;
    expect(headers.authorization).toBe("Bearer gho_user");
    expect(call?.init?.body).toBe(JSON.stringify({ labels: ["factory:plan-approved"] }));
  });

  it("names the fix when GitHub refuses", async () => {
    const gh = fakeFetch(() => new Response("{}", { status: 403 }));
    await expect(addLabel("t", REPO, 1, "x", gh.fetch)).rejects.toMatchObject({
      status: 403,
      message: "GitHub refused the label write (403): your token lacks write access on acme/atlas-api",
    });
    const missing = fakeFetch(() => new Response("{}", { status: 404 }));
    await expect(removeLabel("t", REPO, 1, "factory:ready", missing.fetch)).resolves.toBeUndefined();
    const broken = fakeFetch(() => new Response("{}", { status: 500 }));
    await expect(removeLabel("t", REPO, 1, "x", broken.fetch)).rejects.toBeInstanceOf(GitHubRefused);
  });

  it("reads the viewer's permission and falls back to none", async () => {
    const gh = fakeFetch(() => Response.json({ permission: "write" }));
    expect(await viewerPermission("t", REPO, "rsouza", gh.fetch)).toBe("write");
    expect(gh.calls[0]?.url).toContain("/collaborators/rsouza/permission");
    const refused = fakeFetch(() => new Response("", { status: 403 }));
    expect(await viewerPermission("t", REPO, "rsouza", refused.fetch)).toBe("none");
  });

  it("revokes the token with the App's client credentials at sign-out", async () => {
    const gh = fakeFetch(() => new Response(null, { status: 204 }));
    expect(await revokeToken("Iv1.app", "secret", "gho_user", gh.fetch)).toBe(true);
    const call = gh.calls[0];
    expect(call?.url).toBe("https://api.github.com/applications/Iv1.app/token");
    expect(call?.init?.method).toBe("DELETE");
    const headers = call?.init?.headers as Record<string, string>;
    expect(headers.authorization).toBe(`Basic ${Buffer.from("Iv1.app:secret").toString("base64")}`);
    expect(call?.init?.body).toBe(JSON.stringify({ access_token: "gho_user" }));
  });
});
