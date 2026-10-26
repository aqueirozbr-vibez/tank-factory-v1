"use client";

// Label, `used / cap` in mono, a 10 px bar with an 80% tick, a percent line (ui-context.md ·
// Component Library · Meter). Tone and glyph change together: ● under 80%, ▲ 80 to 99%,
// ■ at the cap. `role="meter"` with a spoken value text.

import styles from "./Meter.module.css";

export type MeterState = "default" | "loading" | "error";

export type MeterProps = {
  label: string;
  used: number;
  cap: number;
  unit?: "usd";
  state?: MeterState;
  note?: string;
};

export type MeterTone = { role: "info" | "warn" | "fail"; glyph: "●" | "▲" | "■"; percent: number };

export function money(value: number): string {
  return `$${value.toFixed(2)}`;
}

export function toneOf(used: number, cap: number): MeterTone {
  const percent = cap > 0 ? Math.min(100, Math.round((used / cap) * 100)) : 0;
  if (cap > 0 && used >= cap) return { role: "fail", glyph: "■", percent: 100 };
  if (percent >= 80) return { role: "warn", glyph: "▲", percent };
  return { role: "info", glyph: "●", percent };
}

export function valueText(used: number, cap: number): string {
  const tone = toneOf(used, cap);
  return `${money(used)} of ${money(cap)}, ${tone.percent} percent`;
}

export function Meter({ label, used, cap, state = "default", note }: MeterProps) {
  const tone = toneOf(used, cap);
  const readable = state === "default";
  const text = readable ? valueText(used, cap) : state === "loading" ? "loading" : "not readable";
  return (
    <div
      className={`${styles.meter} ${styles[tone.role]} ${styles[state]}`}
      role="meter"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={cap}
      aria-valuenow={readable ? used : 0}
      aria-valuetext={`${label}: ${text}`}
      aria-busy={state === "loading" || undefined}
      data-state={state}
    >
      <div className={styles.head}>
        <span className={`${styles.label} mono`}>{label}</span>
        <span className={`${styles.numbers} mono`}>
          {readable ? `${money(used)} / ${money(cap)}` : state === "loading" ? "… / …" : "— / —"}
        </span>
      </div>
      <div className={styles.bar} aria-hidden="true">
        <span className={styles.fill} style={{ width: `${readable ? tone.percent : 0}%` }} />
        <span className={styles.tick} />
      </div>
      <div className={styles.foot}>
        <span className={styles.glyph} aria-hidden="true">
          {readable ? tone.glyph : state === "error" ? "!" : "○"}
        </span>
        <span className="mono">
          {readable ? `${tone.percent}%` : state === "loading" ? "Loading" : "Not readable"}
        </span>
        {note ? <span className="text-muted">{note}</span> : null}
      </div>
    </div>
  );
}
