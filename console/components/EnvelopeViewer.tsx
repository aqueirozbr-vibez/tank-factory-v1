"use client";

// Tabs planner / builder (attempt n) / reviewer; the JSON in mono; a footer with output_type,
// envelope_id, time and Copy JSON (ui-context.md · Component Library · EnvelopeViewer).
// `valid = false` highlights the schema error path and shows the raw text; an empty tab reads
// "No envelope; phase did not return." Radix Tabs carry the keyboard behaviour.

import * as Tabs from "@radix-ui/react-tabs";

import type { EnvelopeView } from "@/lib/task";

import { Chip } from "./Chip";
import { CopyJson } from "./CopyJson";
import styles from "./EnvelopeViewer.module.css";

export type EnvelopeViewerProps = { envelopes: EnvelopeView[] };

export type EnvelopeTab = { key: string; label: string; envelope: EnvelopeView | null };

const ORDER = ["planner", "builder", "reviewer"] as const;

/** One tab per agent; the builder gets one tab per attempt; a missing agent still has a tab. */
export function tabsOf(envelopes: EnvelopeView[]): EnvelopeTab[] {
  const tabs: EnvelopeTab[] = [];
  for (const agent of ORDER) {
    const mine = envelopes.filter((e) => e.agent === agent).sort((a, b) => a.attempt - b.attempt || a.created_at.localeCompare(b.created_at));
    if (mine.length === 0) {
      tabs.push({ key: agent, label: agent, envelope: null });
      continue;
    }
    if (agent === "builder") {
      for (const envelope of mine) {
        tabs.push({ key: `${agent}-${envelope.attempt}-${envelope.envelope_id}`, label: `builder (attempt ${envelope.attempt})`, envelope });
      }
    } else {
      const latest = mine[mine.length - 1];
      if (latest) tabs.push({ key: agent, label: agent, envelope: latest });
    }
  }
  return tabs;
}

export function pretty(payload: unknown): string {
  if (payload === null || payload === undefined) return "";
  if (typeof payload === "string") {
    try {
      return JSON.stringify(JSON.parse(payload), null, 2);
    } catch {
      return payload;
    }
  }
  return JSON.stringify(payload, null, 2);
}

export function violationsOf(envelope: EnvelopeView): Array<{ item: string; problem: string }> {
  if (!Array.isArray(envelope.violations)) return [];
  return (envelope.violations as Array<Record<string, unknown>>).map((v) => ({
    item: String(v["item"] ?? ""),
    problem: String(v["problem"] ?? ""),
  }));
}

export function EnvelopeViewer({ envelopes }: EnvelopeViewerProps) {
  const tabs = tabsOf(envelopes);
  const first = tabs.find((t) => t.envelope !== null) ?? tabs[0];
  return (
    <section aria-labelledby="envelopes-title" className={styles.viewer}>
      <h2 id="envelopes-title" className={styles.heading}>
        Envelopes <span className="mono text-muted">{envelopes.length}</span>
      </h2>
      <Tabs.Root defaultValue={first?.key} className={styles.root}>
        <Tabs.List aria-label="Envelope by agent" className={styles.list}>
          {tabs.map((tab) => (
            <Tabs.Trigger key={tab.key} value={tab.key} className={`${styles.trigger} mono`}>
              {tab.label}
              {tab.envelope && !tab.envelope.valid ? <span className={styles.invalidDot} aria-label="invalid" /> : null}
            </Tabs.Trigger>
          ))}
        </Tabs.List>
        {tabs.map((tab) => (
          <Tabs.Content key={tab.key} value={tab.key} className={styles.content}>
            {tab.envelope ? <EnvelopeBody envelope={tab.envelope} /> : <p className="text-muted">No envelope; phase did not return.</p>}
          </Tabs.Content>
        ))}
      </Tabs.Root>
    </section>
  );
}

export function EnvelopeBody({ envelope }: { envelope: EnvelopeView }) {
  const violations = violationsOf(envelope);
  const raw = typeof envelope.payload === "string" ? envelope.payload : null;
  return (
    <div className={styles.body} data-valid={envelope.valid ? "true" : "false"}>
      {!envelope.valid ? (
        <div className={styles.invalid} role="status">
          <Chip kind="gate" value="fail" title="envelope_schema" />
          <ul className={styles.violations}>
            {violations.length > 0 ? (
              violations.map((v, index) => (
                <li key={index}>
                  <span className={`${styles.path} mono`}>{v.item}</span> {v.problem}
                </li>
              ))
            ) : (
              <li>schema invalid; no error path recorded</li>
            )}
          </ul>
        </div>
      ) : null}
      <pre className={`${styles.json} mono`} tabIndex={0}>{envelope.valid ? pretty(envelope.payload) : (raw ?? pretty(envelope.payload)) || "(empty)"}</pre>
      <footer className={`${styles.footer} mono text-muted`}>
        <span>{envelope.output_type}</span>
        <span>{envelope.envelope_id}</span>
        <span>{envelope.created_at}</span>
        <CopyJson value={envelope.payload} />
      </footer>
    </div>
  );
}
