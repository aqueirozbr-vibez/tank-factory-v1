"use client";

// The leading indicators with their lines, collapsed by default (ui-context.md · Layout Patterns
// · Overview; TRD §9). The collapsed state is remembered per browser, best effort.

import { useEffect, useState } from "react";

import type { MetricView } from "@/lib/overview";

import styles from "./IndicatorsPanel.module.css";

export const PANEL_KEY = "factory-console.indicators-open";

const LABELS: Record<string, string> = {
  repair_rounds_mean: "Repair rounds per task",
  escalation_rate: "Escalation rate",
  review_minutes_median: "Review minutes per PR",
  eval_pass_rate: "Eval pass rate",
  eval_gap_points: "Second-installation eval gap",
  protected_path_attempts: "Protected-path attempts",
  stuck_runs_7d: "Stuck runs, 7 days",
  verdict_override_rate: "Reviewer override rate",
};

export function labelOf(metric: string): string {
  if (metric.startsWith("eval_pass_rate_")) return `Eval pass rate · ${metric.slice("eval_pass_rate_".length)}`;
  return LABELS[metric] ?? metric;
}

export function thresholdText(thresholds: Record<string, number> | null | undefined): string {
  if (!thresholds) return "";
  return Object.entries(thresholds)
    .map(([name, value]) => `${name} ${value}`)
    .join(" · ");
}

export function formatIndicator(metric: string, value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  if (metric.endsWith("_rate") || metric.startsWith("eval_pass_rate")) return `${value.toFixed(1)}%`;
  if (metric === "eval_gap_points") return `${value > 0 ? "+" : ""}${value.toFixed(1)} pt`;
  return Number.isInteger(value) ? String(value) : value.toFixed(2);
}

export type IndicatorsPanelProps = { items: MetricView[]; day: string | null };

export function IndicatorsPanel({ items, day }: IndicatorsPanelProps) {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    try {
      setOpen(window.localStorage.getItem(PANEL_KEY) === "1");
    } catch {
      // best effort
    }
  }, []);
  function toggle(event: React.SyntheticEvent<HTMLDetailsElement>) {
    const next = event.currentTarget.open;
    setOpen(next);
    try {
      window.localStorage.setItem(PANEL_KEY, next ? "1" : "0");
    } catch {
      // best effort
    }
  }
  return (
    <details className={`card blueprint ${styles.panel}`} open={open} onToggle={toggle}>
      <i className="corner tl" />
      <i className="corner tr" />
      <i className="corner bl" />
      <i className="corner br" />
      <summary className={styles.summary}>
        Indicators <span className="mono text-muted">{items.length}{day ? ` · as of ${day}` : ""}</span>
      </summary>
      {items.length === 0 ? (
        <p className="text-muted">No indicator has been materialised yet.</p>
      ) : (
        <table className={`table ${styles.table}`}>
          <thead>
            <tr>
              <th scope="col">Indicator</th>
              <th scope="col" className={styles.num}>
                Value
              </th>
              <th scope="col">Lines</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.metric}>
                <td>{labelOf(item.metric)}</td>
                <td className={`${styles.num} mono`}>{formatIndicator(item.metric, item.value)}</td>
                <td className="mono text-muted">{thresholdText(item.thresholds)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </details>
  );
}
