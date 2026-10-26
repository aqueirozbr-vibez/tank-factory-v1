// The "as of" stamp under every screen title (ui-context.md · Layout Patterns, Copy and Tone).
// Relative wording with the absolute timestamp and offset on hover and focus.

import styles from "./Freshness.module.css";

export type FreshnessProps = { at: Date };

export function absoluteStamp(at: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  const offsetMinutes = -at.getTimezoneOffset();
  const sign = offsetMinutes >= 0 ? "+" : "-";
  const hours = pad(Math.floor(Math.abs(offsetMinutes) / 60));
  return (
    `${at.getFullYear()}-${pad(at.getMonth() + 1)}-${pad(at.getDate())} ` +
    `${pad(at.getHours())}:${pad(at.getMinutes())}:${pad(at.getSeconds())} ${sign}${hours}`
  );
}

export function Freshness({ at }: FreshnessProps) {
  const stamp = absoluteStamp(at);
  return (
    <span className={`${styles.stamp} mono text-muted`} title={stamp} tabIndex={0}>
      as of <time dateTime={at.toISOString()}>{stamp}</time>
    </span>
  );
}
