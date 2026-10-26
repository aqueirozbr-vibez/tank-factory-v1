// Scaffold-only content for a screen whose components arrive in units 58 to 65: the section
// names as the UI context spells them, in an instrument frame, so the route renders its title.

import styles from "./Placeholder.module.css";

export type PlaceholderProps = { screen: string; sections: readonly string[] };

export function Placeholder({ screen, sections }: PlaceholderProps) {
  return (
    <section className={`card blueprint ${styles.frame}`} aria-label={`${screen} sections`}>
      <i className="corner tl" />
      <i className="corner tr" />
      <i className="corner bl" />
      <i className="corner br" />
      <p className="card-kicker mono">{screen}</p>
      <ul className={styles.list}>
        {sections.map((section) => (
          <li key={section}>{section}</li>
        ))}
      </ul>
      <p className={`card-meta ${styles.meta}`}>No data yet. The screen fills in later units.</p>
    </section>
  );
}
