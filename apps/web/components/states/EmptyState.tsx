/**
 * What a section shows when the data it needs is not there yet.
 *
 * The component exists so every section says the same honest thing — what is
 * missing and the command that fills it — instead of drawing an empty chart or
 * a zero that would read as a real measurement. The copy stays generic on
 * purpose: the caller supplies the title, the prose and the command, and this
 * component fetches nothing, so it serves every page of the dashboard.
 */
import type { ReactElement, ReactNode } from "react";

import styles from "./states.module.css";

export type EmptyStateProps = {
  /** The heading of the state; the page outline repeats it as an `h2`. */
  readonly title: string;
  /** The prose that names what is missing. */
  readonly children?: ReactNode;
  /** The command that fills the gap, for example `make ingest`. */
  readonly command?: string;
  /** A control the reader can use instead of the command. */
  readonly action?: ReactNode;
};

export function EmptyState(props: EmptyStateProps): ReactElement {
  const { title, children, command, action } = props;

  return (
    <section className={styles.state}>
      <h2 className={styles.title}>{title}</h2>
      {children ? <div className={styles.prose}>{children}</div> : null}
      {command ? (
        <p className={styles.command}>
          Run <code className={styles.code}>{command}</code> to fill this
          section.
        </p>
      ) : null}
      {action ? <div className={styles.action}>{action}</div> : null}
    </section>
  );
}
