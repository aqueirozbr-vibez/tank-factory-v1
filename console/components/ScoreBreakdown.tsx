"use client";

// A framed card: title and instrument version, rows base × factors with their rule text, the
// equation in a mono box, the outcome chip and its conclusion (ui-context.md · Component
// Library · ScoreBreakdown). An override shows the login and reason under the outcome; tier U
// reads "Scope names no paths" with score 0.

import { useId } from "react";

import { Chip, type ChipKind } from "./Chip";
import styles from "./ScoreBreakdown.module.css";

export type Factor = { name: string; rule: string; value: number };

export type ScoreBreakdownProps = {
  instrument: "risk" | "review";
  version: string | number;
  base: number;
  baseLabel: string;
  factors: readonly Factor[];
  score: number;
  equation: string;
  outcome: string;
  conclusion: string;
  tier?: string;
  override?: { by: string; reason: string } | null;
};

export const TITLES: Record<ScoreBreakdownProps["instrument"], string> = {
  risk: "Risk score",
  review: "Review score",
};

export function outcomeKind(instrument: ScoreBreakdownProps["instrument"]): ChipKind {
  return instrument === "risk" ? "risk" : "verdict";
}

export function ScoreBreakdown(props: ScoreBreakdownProps) {
  const { instrument, version, base, baseLabel, factors, score, equation, outcome, conclusion } = props;
  const unscoped = instrument === "risk" && props.tier === "U";
  const headingId = useId();
  return (
    <section className={`card blueprint ${styles.card}`} aria-labelledby={headingId}>
      <i className="corner tl" />
      <i className="corner tr" />
      <i className="corner bl" />
      <i className="corner br" />
      <header className={styles.head}>
        <h3 id={headingId} className={styles.title}>
          {TITLES[instrument]}
        </h3>
        <span className="mono text-muted">instrument v{version}</span>
      </header>
      {unscoped ? (
        <p className={styles.unscoped}>Scope names no paths. Score 0.</p>
      ) : (
        <table className={`table ${styles.rows}`}>
          <tbody>
            <tr>
              <th scope="row">{baseLabel}</th>
              <td className="mono">{base}</td>
              <td className="text-muted">base</td>
            </tr>
            {factors.map((factor) => (
              <tr key={factor.name}>
                <th scope="row">× {factor.name}</th>
                <td className="mono">{factor.value}</td>
                <td className="text-muted">{factor.rule}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <pre className={`${styles.equation} mono`}>{unscoped ? "0" : equation}</pre>
      <div className={styles.outcome}>
        <Chip kind={outcomeKind(instrument)} value={outcome} />
        <span>{conclusion}</span>
      </div>
      {props.override ? (
        <p className={`${styles.override} text-muted`}>
          Overridden by <span className="mono">@{props.override.by}</span>: {props.override.reason}
        </p>
      ) : null}
      <p className={`${styles.score} mono text-muted`}>score {unscoped ? 0 : score}</p>
    </section>
  );
}
