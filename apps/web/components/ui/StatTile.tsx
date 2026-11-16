import type { ReactElement } from "react";

import { MetricHelp } from "./MetricHelp";
import styles from "./ui.module.css";

/**
 * One headline number with its label and its meaning.
 *
 * The tone is a small accent only: the sub-line text states whether the number
 * is good or bad, so the meaning survives greyscale printing, a colour-blind
 * reader and a screen reader. The tile holds no state and renders on the
 * server.
 */
export type StatTone = "neutral" | "good" | "bad";

export type StatTileHelp = {
  /** What the metric measures. */
  readonly definition: string;
  /** Which direction of the value is better, when that is not obvious. */
  readonly direction?: "lower-is-better" | "higher-is-better";
};

export type StatTileProps = {
  /** What the value measures. */
  readonly label: string;
  /** The formatted value, already rounded and with its unit. */
  readonly value: string;
  /** The sentence that explains or qualifies the value. */
  readonly subline?: string;
  /** The accent only; the sub-line carries the meaning. */
  readonly tone?: StatTone;
  /** The metric definition, shown in a `MetricHelp` beside the label. */
  readonly help?: StatTileHelp;
};

const TONE_CLASSES: Readonly<Record<StatTone, string>> = {
  neutral: styles.toneNeutral,
  good: styles.toneGood,
  bad: styles.toneBad,
};

export function StatTile(props: StatTileProps): ReactElement {
  const { label, value, subline, tone = "neutral", help } = props;

  return (
    <div className={`${styles.statTile} ${TONE_CLASSES[tone]}`}>
      <div className={styles.statTileHeader}>
        <span className={styles.statTileLabel}>{label}</span>
        {help ? (
          <MetricHelp
            label={label}
            definition={help.definition}
            direction={help.direction}
          />
        ) : null}
      </div>
      <p className={styles.statTileValue}>{value}</p>
      {subline ? <p className={styles.statTileSubline}>{subline}</p> : null}
    </div>
  );
}
