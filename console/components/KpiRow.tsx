// Five KPITile on a --space-6 gutter grid (ui-context.md · Layout Patterns · Overview).
import { HEADLINE_TILES, targetText, tileStatus, type MetricView } from "@/lib/overview";

import { KPITile } from "./KPITile";
import styles from "./KpiRow.module.css";

export type KpiRowProps = { items: MetricView[]; day: string | null; loading?: boolean };

export function KpiRow({ items, day, loading = false }: KpiRowProps) {
  return (
    <section aria-label="Headline KPIs" className={styles.row}>
      {HEADLINE_TILES.map((tile) => {
        const { value, status, note } = tileStatus(tile, items);
        const noteText = note ?? (day ? `as of ${day}` : undefined);
        return (
          <KPITile
            key={tile.metric}
            label={tile.label}
            value={value}
            unit={tile.unit}
            decimals={tile.decimals}
            status={loading ? "loading" : status}
            target={targetText(tile.metric)}
            {...(noteText ? { note: noteText } : {})}
          />
        );
      })}
    </section>
  );
}
