/**
 * What a section shows when the API answered but refused the request.
 *
 * The dashboard reads every number from the API, so a rejected read must be
 * told apart from an empty one. This component renders only the problem's title
 * and detail: the raw body, the status code as a headline and a stack trace
 * belong in the server log, not in front of a reader. When the API sent no
 * explanation, the neutral fallback sentence says so instead of inventing a
 * cause.
 */
import type { ReactElement, ReactNode } from "react";

import type { Problem } from "@/lib/api";

import styles from "./states.module.css";

export type ProblemStateProps = {
  /** The problem details body, or `null` when the API sent none. */
  readonly problem: Problem | null;
  /** The heading used when the problem carries no title of its own. */
  readonly fallbackTitle?: string;
  /** A control that offers a way forward, such as a link to another page. */
  readonly action?: ReactNode;
};

const DEFAULT_FALLBACK_TITLE = "The API rejected this request";

const NO_DETAIL = "The API returned no description of this failure.";

export function ProblemState(props: ProblemStateProps): ReactElement {
  const { problem, fallbackTitle = DEFAULT_FALLBACK_TITLE, action } = props;
  const title = problem?.title ?? fallbackTitle;
  const detail = problem?.detail;

  return (
    <section className={styles.state}>
      <h2 className={styles.title}>{title}</h2>
      <p className={styles.prose}>{detail ? detail : NO_DETAIL}</p>
      {action ? <div className={styles.action}>{action}</div> : null}
    </section>
  );
}
