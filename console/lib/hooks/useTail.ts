"use client";

// The live tail (code-standards · Next.js; TRD §3.4, §5.2): an EventSource on the console's SSE
// proxy that resumes from the last seq, a 4 s poll when the stream fails, "Tail behind" when
// the newest row's age passes 5 s while rows keep coming, pause and resume without losing rows,
// and earlier pages merged in behind the newest one.

import { useCallback, useEffect, useRef, useState } from "react";
import { z } from "zod";

export const POLL_FALLBACK_MS = 4000;
export const BEHIND_AFTER_S = 5;

export const eventRowSchema = z.object({
  seq: z.number().int(),
  kind: z.string(),
  actor: z.string(),
  task_id: z.string().nullable(),
  run_id: z.string().nullable(),
  phase_id: z.string().nullable(),
  parent_seq: z.number().int().nullable(),
  payload: z.unknown(),
  tokens: z.number().nullable(),
  started_at: z.string().nullable(),
  ended_at: z.string().nullable(),
  created_at: z.string(),
  hash: z.string(),
});

export type TailRow = z.infer<typeof eventRowSchema>;

export type Transport = "sse" | "poll" | "off";

export type TailState = {
  rows: TailRow[];
  lastSeq: number;
  paused: boolean;
  transport: Transport;
  behindBy: number | null;
  newestAt: string | null;
  error: string | null;
};

export type UseTailOptions = {
  taskId: string;
  initialRows: TailRow[];
  initialLastSeq: number;
  enabled?: boolean;
  maxRows?: number;
};

export function eventsUrl(taskId: string, query: Record<string, string | number>): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) params.set(key, String(value));
  return `/api/console/tasks/${encodeURIComponent(taskId)}/events?${params.toString()}`;
}

export function streamUrl(taskId: string, after: number): string {
  return `/api/console/tasks/${encodeURIComponent(taskId)}/stream?after=${after}`;
}

/** Newest first, no duplicate seq, at most `maxRows` (the oldest fall off). */
export function mergeRows(existing: TailRow[], incoming: TailRow[], maxRows: number): TailRow[] {
  const seen = new Set(existing.map((r) => r.seq));
  const fresh = incoming.filter((r) => !seen.has(r.seq));
  if (fresh.length === 0) return existing;
  const merged = [...fresh, ...existing].sort((a, b) => b.seq - a.seq);
  return merged.length > maxRows ? merged.slice(0, maxRows) : merged;
}

export function behindSeconds(newestAt: string | null, now: Date, streaming: boolean): number | null {
  if (!newestAt || !streaming) return null;
  const age = (now.getTime() - new Date(newestAt).getTime()) / 1000;
  return age > BEHIND_AFTER_S ? Math.round(age) : null;
}

export function parseRows(items: unknown[]): TailRow[] {
  return items.map((item) => eventRowSchema.parse(item));
}

export type Tail = TailState & {
  pause: () => void;
  resume: () => void;
  addOlder: (rows: TailRow[]) => void;
};

export function useTail({ taskId, initialRows, initialLastSeq, enabled = true, maxRows = 10_000 }: UseTailOptions): Tail {
  const [rows, setRows] = useState<TailRow[]>(initialRows);
  const [paused, setPaused] = useState(false);
  const [transport, setTransport] = useState<Transport>("off");
  const [error, setError] = useState<string | null>(null);
  const [behindBy, setBehindBy] = useState<number | null>(null);
  const [, bump] = useState(0);
  const lastSeq = useRef(initialLastSeq);
  const newestAt = useRef<string | null>(initialRows[0]?.created_at ?? null);
  const buffered = useRef<TailRow[]>([]);
  const pausedRef = useRef(false);
  const transportRef = useRef<Transport>("off");

  const setTransportBoth = useCallback((value: Transport) => {
    transportRef.current = value;
    setTransport(value);
  }, []);

  const accept = useCallback(
    (incoming: TailRow[]) => {
      for (const row of incoming) {
        lastSeq.current = Math.max(lastSeq.current, row.seq);
        if (!newestAt.current || row.created_at > newestAt.current) newestAt.current = row.created_at;
      }
      if (pausedRef.current) {
        buffered.current = mergeRows(buffered.current, incoming, maxRows);
        bump((n) => n + 1); // the cursor moved although the rows did not
        return;
      }
      setRows((current) => mergeRows(current, incoming, maxRows));
    },
    [maxRows],
  );

  useEffect(() => {
    if (!enabled) return;
    let source: EventSource | null = null;
    let poll: number | null = null;
    let closed = false;

    function startPoll() {
      if (poll !== null) return;
      setTransportBoth("poll");
      poll = window.setInterval(async () => {
        try {
          const response = await fetch(eventsUrl(taskId, { after: lastSeq.current, limit: 200 }), { cache: "no-store" });
          if (!response.ok) return;
          const body = (await response.json()) as { items: unknown[] };
          const parsed = parseRows(body.items);
          if (parsed.length) accept(parsed);
        } catch {
          // the next tick tries again
        }
      }, POLL_FALLBACK_MS);
    }

    function startStream() {
      source = new EventSource(streamUrl(taskId, lastSeq.current));
      source.addEventListener("row", (event) => {
        try {
          const row = eventRowSchema.parse(JSON.parse((event as MessageEvent<string>).data));
          accept([row]);
          setTransportBoth("sse");
        } catch (parseError) {
          setError(parseError instanceof Error ? parseError.message : "a row did not parse");
        }
      });
      source.addEventListener("ping", () => setTransportBoth("sse"));
      source.onopen = () => setTransportBoth("sse");
      source.onerror = () => {
        if (closed) return;
        source?.close();
        source = null;
        startPoll();
      };
    }

    if (typeof EventSource === "undefined") startPoll();
    else startStream();

    const behind = window.setInterval(() => {
      setBehindBy(behindSeconds(newestAt.current, new Date(), transportRef.current !== "off" && !pausedRef.current));
    }, 1000);

    return () => {
      closed = true;
      source?.close();
      if (poll !== null) window.clearInterval(poll);
      window.clearInterval(behind);
      setTransportBoth("off");
    };
  }, [taskId, enabled, accept, setTransportBoth]);

  const pause = useCallback(() => {
    pausedRef.current = true;
    setPaused(true);
  }, []);

  const resume = useCallback(() => {
    pausedRef.current = false;
    setPaused(false);
    if (buffered.current.length) {
      const held = buffered.current;
      buffered.current = [];
      setRows((current) => mergeRows(current, held, maxRows));
    }
  }, [maxRows]);

  const addOlder = useCallback(
    (older: TailRow[]) => {
      if (older.length === 0) return;
      setRows((current) => mergeRows(current, older, maxRows));
    },
    [maxRows],
  );

  return {
    rows,
    lastSeq: lastSeq.current,
    paused,
    transport,
    behindBy,
    newestAt: newestAt.current,
    error,
    pause,
    resume,
    addOlder,
  };
}
