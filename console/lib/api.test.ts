import { describe, expect, it } from "vitest";

import { ApiError, taskPath, withQuery } from "./api";

describe("api client helpers", () => {
  it("encodes a task id as one path segment", () => {
    expect(taskPath("acme/atlas-api#418")).toBe("/api/v1/tasks/acme%2Fatlas-api%23418");
    expect(taskPath("acme/atlas-api#418", "/events")).toBe(
      "/api/v1/tasks/acme%2Fatlas-api%23418/events",
    );
  });

  it("builds a query string and drops undefined values", () => {
    expect(withQuery("/api/v1/tasks", { status: "blocked", limit: 50, cursor: undefined })).toBe(
      "/api/v1/tasks?status=blocked&limit=50",
    );
    expect(withQuery("/api/v1/kpis", undefined)).toBe("/api/v1/kpis");
  });

  it("carries the orchestrator's error body", () => {
    const error = new ApiError(429, {
      code: "rate_limited",
      message: "rate limit read: 300 per 60 s per login",
      traceId: "abc",
    });
    expect(error.status).toBe(429);
    expect(error.message).toBe("rate_limited: rate limit read: 300 per 60 s per login");
    expect(error.body.traceId).toBe("abc");
  });
});
