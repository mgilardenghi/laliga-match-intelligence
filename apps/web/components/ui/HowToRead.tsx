import type { ReactElement } from "react";

import styles from "./ui.module.css";

/**
 * The short reading guide a page can close with.
 *
 * The list is capped, because a guide longer than a few pairs stops being read;
 * an empty list renders nothing at all rather than an empty panel. The
 * component is a Server Component: the pairs arrive as props and nothing here
 * fetches or holds state.
 */
export type HowToReadItem = {
  readonly term: string;
  readonly definition: string;
};

export type HowToReadProps = {
  /** The panel heading; defaults to `How to read this page`. */
  readonly title?: string;
  readonly items: readonly HowToReadItem[];
};

const DEFAULT_TITLE = "How to read this page";
const MAX_ITEMS = 4;

export function HowToRead(props: HowToReadProps): ReactElement | null {
  const { title = DEFAULT_TITLE, items } = props;

  if (items.length === 0) {
    return null;
  }

  const shown = items.slice(0, MAX_ITEMS);

  return (
    <section className={styles.howToRead}>
      <h2 className={styles.howToReadTitle}>{title}</h2>
      <dl className={styles.howToReadList}>
        {shown.map((item) => (
          <div key={item.term} className={styles.howToReadItem}>
            <dt className={styles.howToReadTerm}>{item.term}</dt>
            <dd className={styles.howToReadDefinition}>{item.definition}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
