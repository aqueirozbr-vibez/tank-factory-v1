"use client";

// The live trace (ui-context.md · Component Library · TraceTable; TRD §3.4): newest first, the
// tail on, a new row tinted for 2 s, 30 px rows; header "live · newest row 2 s ago · 10,000
// rows · cursor seq > 48211 · Pause tail"; paused reads "Resume tail"; "Tail behind by 34 s"
// keeps the rows; `p` toggles. The first paint holds the newest page; earlier pages load as the
// list is scrolled to its end (or on the button) through the console's events proxy; the DOM
// holds a window over the newest rows.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { eventsUrl, parseRows, useTail, type TailRow } from "@/lib/hooks/useTail";

import { isTypingTarget } from "./KeyboardMap";
import styles from "./TraceTable.module.css";

export type TraceTableProps = {
  taskId: string;
  initialRows: TailRow[];
  initialLastSeq: number;
  oldestSeq: number | null;
  moreBefore: boolean;
  live?: boolean;
};

export const WINDOW = 400; // rows in the DOM at once; the rest sit behind the window
export const TINT_MS = 2000;
export const PAGE = 200;

export function summary(row: TailRow): { tool: string; args: string; ok: string; duration: string; snippet: string } {
  const payload = (row.payload ?? {}) as Record<string, unknown>;
  const tool = typeof payload["tool"] === "string" ? payload["tool"] : row.kind;
  const args = payload["args"] !== undefined ? JSON.stringify(payload["args"]) : JSON.stringify(payload);
  const ok = payload["ok"] === undefined ? "–" : payload["ok"] ? "✓" : "×";
  const ms = payload["duration_ms"];
  const duration = typeof ms === "number" ? `${ms.toLocaleString("en-US")} ms` : "–";
  const snippet = typeof payload["result_snippet"] === "string" ? payload["result_snippet"] : "";
  return {
    tool,
    args: args.length > 120 ? `${args.slice(0, 117)}…` : args,
    ok,
    duration,
    snippet: snippet.length > 80 ? `${snippet.slice(0, 77)}…` : snippet,
  };
}

export function ago(iso: string | null, now: Date): string {
  if (!iso) return "–";
  const seconds = Math.max(0, Math.round((now.getTime() - new Date(iso).getTime()) / 1000));
  if (seconds < 60) return `${seconds} s ago`;
  const minutes = Math.round(seconds / 60);
  return minutes < 60 ? `${minutes} min ago` : `${Math.round(minutes / 60)} h ago`;
}

export function headerText(input: {
  paused: boolean;
  transport: "sse" | "poll" | "off";
  newestAt: string | null;
  rows: number;
  lastSeq: number;
  now: Date;
}): string {
  const transport = input.transport === "sse" ? "live" : input.transport === "poll" ? "polling every 4 s" : "tail off";
  return [
    input.paused ? "paused" : transport,
    `newest row ${ago(input.newestAt, input.now)}`,
    `${input.rows.toLocaleString("en-US")} rows`,
    `cursor seq > ${input.lastSeq}`,
  ].join(" · ");
}

export function TraceTable({ taskId, initialRows, initialLastSeq, oldestSeq, moreBefore, live = true }: TraceTableProps) {
  const tail = useTail({ taskId, initialRows, initialLastSeq, enabled: live });
  const [now, setNow] = useState(() => new Date());
  const [shown, setShown] = useState(WINDOW);
  const [older, setOlder] = useState<{ oldest: number | null; more: boolean; loading: boolean; error: string | null }>({
    oldest: oldestSeq,
    more: moreBefore,
    loading: false,
    error: null,
  });
  const freshSince = useRef<Map<number, number>>(new Map());
  const sentinel = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key !== "p" || event.metaKey || event.ctrlKey || event.altKey || isTypingTarget(event.target)) return;
      if (tail.paused) tail.resume();
      else tail.pause();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [tail]);

  const loadEarlier = useCallback(async () => {
    if (older.loading || !older.more || older.oldest === null) return;
    setOlder((state) => ({ ...state, loading: true, error: null }));
    try {
      const response = await fetch(eventsUrl(taskId, { before: older.oldest, limit: PAGE }), { cache: "no-store" });
      if (!response.ok) throw new Error(`earlier rows: the console answered ${response.status}`);
      const body = (await response.json()) as { items: unknown[]; next?: string | null };
      const rows = parseRows(body.items);
      tail.addOlder(rows);
      const oldest = rows.length ? Math.min(...rows.map((r) => r.seq)) : older.oldest;
      setOlder({ oldest, more: body.next !== null && body.next !== undefined, loading: false, error: null });
      setShown((n) => n + rows.length);
    } catch (error) {
      setOlder((state) => ({ ...state, loading: false, error: error instanceof Error ? error.message : "earlier rows failed" }));
    }
  }, [older, taskId, tail]);

  useEffect(() => {
    const node = sentinel.current;
    if (!node || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) {
        if (tail.rows.length > shown) setShown((n) => n + WINDOW);
        else void loadEarlier();
      }
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, [loadEarlier, shown, tail.rows.length]);

  const rows = useMemo(() => tail.rows.slice(0, shown), [tail.rows, shown]);
  const stamp = Date.now();
  for (const row of rows) {
    if (!freshSince.current.has(row.seq)) freshSince.current.set(row.seq, stamp);
  }
  const header = headerText({
    paused: tail.paused,
    transport: tail.transport,
    newestAt: tail.newestAt,
    rows: tail.rows.length,
    lastSeq: tail.lastSeq,
    now,
  });
  const hidden = tail.rows.length - rows.length;

  return (
    <section className={styles.trace} aria-labelledby="trace-title">
      <div className={styles.header}>
        <h2 id="trace-title" className={styles.title}>
          Trace
        </h2>
        <span className={`${styles.meta} mono text-muted`} data-testid="trace-header">
          {header}
        </span>
        <button
          type="button"
          className="btn btn-secondary"
          onClick={() => (tail.paused ? tail.resume() : tail.pause())}
          aria-pressed={tail.paused}
        >
          {tail.paused ? "Resume tail" : "Pause tail"}
        </button>
      </div>
      {tail.behindBy !== null && !tail.paused ? (
        <p className={styles.behind} role="status">
          Tail behind by {tail.behindBy} s
        </p>
      ) : null}
      {tail.error || older.error ? (
        <p className={styles.behind} role="status">
          {tail.error ?? older.error}
        </p>
      ) : null}
      <div className={styles.scroll} tabIndex={0} aria-label="Trace rows">
        <table className={`table ${styles.table}`} data-testid="trace-table">
          <thead>
            <tr>
              <th scope="col">started_at</th>
              <th scope="col">agent</th>
              <th scope="col">tool</th>
              <th scope="col">args</th>
              <th scope="col">result_snippet</th>
              <th scope="col">ok</th>
              <th scope="col" className={styles.num}>
                duration_ms
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const s = summary(row);
              const fresh = stamp - (freshSince.current.get(row.seq) ?? 0) < TINT_MS;
              return (
                <tr key={row.seq} className={fresh ? styles.fresh : undefined} data-seq={row.seq} data-fresh={fresh ? "true" : undefined}>
                  <td className="mono" title={row.created_at}>
                    {(row.started_at ?? row.created_at).slice(11, 23)}
                  </td>
                  <td className="mono">{row.actor}</td>
                  <td className="mono">{s.tool}</td>
                  <td className={`mono ${styles.args}`} title={s.args}>
                    {s.args}
                  </td>
                  <td className={styles.args}>{s.snippet}</td>
                  <td className="mono">{s.ok}</td>
                  <td className={`mono ${styles.num}`}>{s.duration}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <div ref={sentinel} className={styles.sentinel} aria-hidden="true" />
        {hidden > 0 || older.more ? (
          <button
            type="button"
            className="btn btn-ghost"
            disabled={older.loading}
            onClick={() => (hidden > 0 ? setShown((n) => n + WINDOW) : void loadEarlier())}
          >
            {older.loading ? "Loading earlier rows…" : hidden > 0 ? `Earlier rows (${hidden.toLocaleString("en-US")} loaded)` : "Earlier rows"}
          </button>
        ) : (
          <p className={`${styles.end} mono text-muted`}>start of the trace</p>
        )}
      </div>
    </section>
  );
}
