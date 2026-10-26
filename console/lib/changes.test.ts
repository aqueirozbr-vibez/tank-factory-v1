import { describe, expect, it } from "vitest";

import { parseSse, tagsFor } from "./changes";

describe("the /changes listener", () => {
  it("parses complete SSE frames and keeps the remainder", () => {
    const buffer =
      'event: config_loaded\ndata: {"config_sha":"abc"}\n\n' +
      'event: eval_complete\nid: 7\ndata: {"run_id":"r1","set_id":"s1","pass_rate":60,"blocked":false}\n\n' +
      'event: ping\ndata: {"last_seq":12}\n\n' +
      "event: task_changed\ndata: {\"task_id\":";
    const { events, rest } = parseSse(buffer);
    expect(events.map((e) => e.event)).toEqual(["config_loaded", "eval_complete", "ping"]);
    expect(events[1]?.data).toMatchObject({ run_id: "r1" });
    expect(rest).toBe("event: task_changed\ndata: {\"task_id\":");
  });

  it("maps notices to the cache tags the configuration and eval reads carry", () => {
    expect(tagsFor({ event: "config_loaded", data: { config_sha: "abc" } })).toEqual(["config", "config:abc"]);
    expect(tagsFor({ event: "eval_complete", data: { run_id: "r1" } })).toEqual(["eval", "eval:r1"]);
    expect(tagsFor({ event: "eval_complete", data: null })).toEqual(["eval"]);
    expect(tagsFor({ event: "task_changed", data: { task_id: "t" } })).toEqual([]);
    expect(tagsFor({ event: "ping", data: {} })).toEqual([]);
  });
});
