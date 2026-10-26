"use client";

// One anatomy for every enumerated value (ui-context.md · Component Library · Chip): glyph,
// the PRD value in mono, a 9% tinted ground and a 1 px border of the same semantic role.
// Colour never carries meaning alone: the glyph and the text value always travel with it.

import styles from "./Chip.module.css";

export type ChipKind = "status" | "risk" | "gate" | "verdict" | "severity" | "surface" | "label";
export type Role = "pass" | "fail" | "warn" | "info" | "neutral";

export type ChipProps = {
  kind: ChipKind;
  value: string;
  disabled?: boolean;
  title?: string;
};

type Look = { role: Role; glyph: string; solid?: boolean; dashed?: boolean };

const STATUS: Record<string, Look> = {
  queued: { role: "neutral", glyph: "○" },
  running: { role: "info", glyph: "●" },
  blocked: { role: "warn", glyph: "■" },
  pr_open: { role: "info", glyph: "◆" },
  merged: { role: "pass", glyph: "✓" },
  cancelled: { role: "neutral", glyph: "×" },
};

const RISK: Record<string, Look> = {
  "risk:low": { role: "neutral", glyph: "▮▯▯" },
  "risk:high": { role: "warn", glyph: "▮▮▯" },
  "risk:governed": { role: "fail", glyph: "▮▮▮" },
};

const GATE: Record<string, Look> = {
  pass: { role: "pass", glyph: "✓" },
  fail: { role: "fail", glyph: "×" },
  running: { role: "info", glyph: "●" },
  pending: { role: "neutral", glyph: "○" },
  skipped: { role: "neutral", glyph: "–", dashed: true },
};

const VERDICT: Record<string, Look> = {
  accepted: { role: "pass", glyph: "✓" },
  revision_needed: { role: "warn", glyph: "↻" },
  human_decision_needed: { role: "fail", glyph: "!" },
};

const SEVERITY: Record<string, Look> = {
  Blocker: { role: "fail", glyph: "!", solid: true },
  Major: { role: "warn", glyph: "■" },
  Minor: { role: "info", glyph: "●" },
  Nit: { role: "neutral", glyph: "○" },
  Unverifiable: { role: "neutral", glyph: "–", dashed: true },
};

const SURFACE: Record<string, Look> = {
  GH: { role: "neutral", glyph: "" },
  FC: { role: "info", glyph: "" },
};

const TABLES: Record<ChipKind, Record<string, Look>> = {
  status: STATUS,
  risk: RISK,
  gate: GATE,
  verdict: VERDICT,
  severity: SEVERITY,
  surface: SURFACE,
  label: {},
};

const NAMES: Record<ChipKind, string> = {
  status: "Task state",
  risk: "Risk",
  gate: "Gate",
  verdict: "Verdict",
  severity: "Severity",
  surface: "Surface",
  label: "Label",
};

/** The role, glyph and shape a value takes; an unknown value is neutral with no glyph. */
export function lookOf(kind: ChipKind, value: string): Look {
  if (kind === "label") return { role: "neutral", glyph: "⌗" };
  return TABLES[kind][value] ?? { role: "neutral", glyph: "" };
}

export function ariaLabelOf(kind: ChipKind, value: string): string {
  return `${NAMES[kind]}: ${value}`;
}

export function Chip({ kind, value, disabled = false, title }: ChipProps) {
  const look = lookOf(kind, value);
  const classes = [styles.chip, styles[look.role]];
  if (look.solid) classes.push(styles.solid);
  if (look.dashed) classes.push(styles.dashed);
  if (kind === "label") classes.push(styles.label);
  if (disabled) classes.push(styles.disabled);
  const label = title ? `${ariaLabelOf(kind, value)}, ${title}` : ariaLabelOf(kind, value);
  return (
    <span
      className={classes.join(" ")}
      role="img"
      aria-label={disabled ? `${label}, disabled` : label}
      data-disabled={disabled ? "true" : undefined}
      data-kind={kind}
      data-role={look.role}
      title={title}
    >
      {look.glyph ? (
        <span className={styles.glyph} aria-hidden="true">
          {look.glyph}
        </span>
      ) : null}
      <span className={styles.value}>{value}</span>
    </span>
  );
}
