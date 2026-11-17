import type { ReactElement } from "react";

import styles from "./ui.module.css";

/**
 * A metric definition behind a native disclosure.
 *
 * The control is a real `<summary>`, so Enter, Space, a click and a tap all
 * toggle it without a hook or a line of JavaScript. The summary carries the
 * metric's name as its accessible name; the mark itself is decorative and drawn in CSS, because
 * the ⓘ character has no glyph in some font stacks and showed as a box. An
 * `<abbr title>` would show the definition only on hover, which a touch reader
 * and a keyboard reader cannot reach.
 */
export type MetricHelpProps = {
  /** The metric the help describes; also names the control. */
  readonly label: string;
  /** What the metric measures. */
  readonly definition: string;
  /** Which direction of the value is better, when that is not obvious. */
  readonly direction?: "lower-is-better" | "higher-is-better";
};

const DIRECTION_SENTENCES: Readonly<
  Record<NonNullable<MetricHelpProps["direction"]>, string>
> = {
  "lower-is-better": "A lower value is better.",
  "higher-is-better": "A higher value is better.",
};

export function MetricHelp(props: MetricHelpProps): ReactElement {
  const { label, definition, direction } = props;

  return (
    <details className={styles.metricHelp}>
      <summary className={styles.metricHelpSummary} aria-label={`What ${label} means`}>
        <span className={styles.metricHelpGlyph} aria-hidden="true" />
      </summary>
      <div className={styles.metricHelpPanel}>
        <p className={styles.metricHelpText}>{definition}</p>
        {direction ? (
          <p className={styles.metricHelpText}>{DIRECTION_SENTENCES[direction]}</p>
        ) : null}
      </div>
    </details>
  );
}
