"use client";

// Label in mono caps, a 40 px value with its unit, seven trend bars (the last one accent, the
// others at 22% ink), a status line with its glyph, the target text and an optional note
// (ui-context.md · Component Library · KPITile). `role="group"` with a full spoken label.

import styles from "./KPITile.module.css";

export type KpiStatus = "on" | "off" | "nodata" | "loading" | "error";

export type KPITileProps = {
  label: string;
  value: number | null;
  unit?: string;
  decimals?: number;
  trend?: readonly number[];
  status: KpiStatus;
  target: string;
  note?: string;
};

const STATUS_LINE: Record<KpiStatus, { glyph: string; text: string; role: string }> = {
  on: { glyph: "✓", text: "on target", role: "pass" },
  off: { glyph: "×", text: "off target", role: "fail" },
  nodata: { glyph: "○", text: "no data yet", role: "neutral" },
  loading: { glyph: "○", text: "loading", role: "neutral" },
  error: { glyph: "!", text: "not readable", role: "fail" },
};

export function formatValue(value: number | null, unit: string | undefined, decimals: number): string {
  if (value === null || Number.isNaN(value)) return "—";
  const number = value.toFixed(decimals);
  if (unit === "%") return `${number}%`;
  if (unit === "$") return `$${number}`;
  return unit ? `${number} ${unit}` : number;
}

export function KPITile({
  label,
  value,
  unit,
  decimals = 1,
  trend = [],
  status,
  target,
  note,
}: KPITileProps) {
  const line = STATUS_LINE[status];
  const readable = status === "on" || status === "off";
  const shown = readable ? formatValue(value, unit, decimals) : "—";
  const bars = trend.slice(-7);
  const max = Math.max(1, ...bars.map((b) => Math.abs(b)));
  const spoken = `${label}: ${shown}, ${line.text}, target ${target}${note ? `, ${note}` : ""}`;
  return (
    <section
      className={`card blueprint ${styles.tile}`}
      role="group"
      aria-label={spoken}
      aria-busy={status === "loading" || undefined}
      data-status={status}
      data-role={line.role}
    >
      <i className="corner tl" />
      <i className="corner tr" />
      <i className="corner bl" />
      <i className="corner br" />
      <p className={`${styles.label} mono`}>{label}</p>
      <p className={styles.value}>{shown}</p>
      <div className={styles.trend} aria-hidden="true">
        {bars.map((bar, index) => (
          <span
            key={index}
            className={`${styles.bar} ${index === bars.length - 1 ? styles.barLast : ""}`}
            style={{ height: `${Math.max(8, Math.round((Math.abs(bar) / max) * 100))}%` }}
          />
        ))}
      </div>
      <p className={`${styles.status} ${styles[line.role]}`}>
        <span aria-hidden="true">{line.glyph}</span> {line.text}
      </p>
      <p className={`${styles.target} text-muted`}>target {target}</p>
      {note ? <p className={`${styles.note} text-muted`}>{note}</p> : null}
    </section>
  );
}
