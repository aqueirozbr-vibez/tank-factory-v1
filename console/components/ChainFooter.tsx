// "chain verified · n records · head …" from the orchestrator's verify routine over the task's
// filter plus its two chain neighbours (TRD §4.3; ui-context.md · Component Library).

import type { components } from "@/lib/api.types";

import styles from "./ChainFooter.module.css";

export type ChainView = components["schemas"]["ChainFooter"];

export function chainText(chain: ChainView): string {
  const head = chain.head_hash ? `head ${chain.head_hash.slice(0, 12)}…` : "no head";
  if (chain.verified) return `chain verified · ${chain.records.toLocaleString("en-US")} records · ${head}`;
  const where = chain.first_bad_seq !== null && chain.first_bad_seq !== undefined ? ` · first bad seq ${chain.first_bad_seq}` : "";
  return `chain broken${where} · ${chain.records.toLocaleString("en-US")} records · ${head}`;
}

export function ChainFooter({ chain }: { chain: ChainView }) {
  return (
    <footer className={`${styles.footer} mono`} data-verified={chain.verified ? "true" : "false"} role="status">
      <span aria-hidden="true">{chain.verified ? "✓" : "×"}</span> {chainText(chain)}
    </footer>
  );
}
