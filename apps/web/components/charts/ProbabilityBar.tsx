import type { ReactElement, ReactNode } from "react";
import { ChartFigure } from "./ChartFigure";
import { chartIds } from "./chart-ids";
import { formatPercent } from "./format";
import styles from "./charts.module.css";

export type ProbabilityBarProps = {
  readonly home: { readonly name: string; readonly probability: number };
  readonly draw: { readonly probability: number };
  readonly away: { readonly name: string; readonly probability: number };
  readonly id?: string;
  readonly title?: string;
  readonly description?: string;
  readonly note?: ReactNode;
};

const DEFAULT_TITLE = "Home win, draw and away win probabilities";
const DEFAULT_DESCRIPTION =
  "Stacked bar of the home win, draw and away win probabilities. Every segment is labelled with its outcome letter and percentage.";

type Segment = {
  readonly key: "H" | "D" | "A";
  readonly probability: number;
  /** The semantic fill of one side of the result, shared by the bar and the swatch. */
  readonly fill: string;
};

type LabelledSegment = Segment & {
  /** This segment's share of the total, from 0 to 1. */
  readonly share: number;
};

export function ProbabilityBar(props: ProbabilityBarProps): ReactElement {
  const { home, draw, away, id, title = DEFAULT_TITLE, description = DEFAULT_DESCRIPTION, note } =
    props;
  const ids = chartIds(id ?? title);

  const segments: readonly Segment[] = [
    { key: "H", probability: home.probability, fill: styles.fillHome },
    { key: "D", probability: draw.probability, fill: styles.fillDraw },
    { key: "A", probability: away.probability, fill: styles.fillAway },
  ];
  const total = segments.reduce((sum, segment) => sum + Math.max(0, segment.probability), 0);
  // A bar whose widths disagree with its own labels would be a lie. Each
  // segment is drawn as its share of the total, and its label shows that same
  // share. A total that is not 100 % is stated in the chart; the table keeps
  // the raw inputs.
  const denominator = total > 0 ? total : 1;
  const scaled = Math.abs(total - 1) > 1e-6;
  const labelled: readonly LabelledSegment[] = segments.map((segment) => ({
    ...segment,
    share: Math.max(0, segment.probability) / denominator,
  }));

  return (
    <ChartFigure
      ids={ids}
      title={title}
      description={description}
      table={{
        caption: "Outcome probabilities",
        columns: ["Outcome", "Team", "Probability"],
        rows: [
          ["H", home.name, formatPercent(home.probability)],
          ["D", "Draw", formatPercent(draw.probability)],
          ["A", away.name, formatPercent(away.probability)],
        ],
      }}
      note={note}
    >
      <div
        role="img"
        aria-labelledby={ids.labelledBy}
        className={`${styles.plot} ${styles.probabilityChart}`}
      >
        <div className={styles.probabilityNames}>
          <span className={styles.probabilityName}>{home.name}</span>
          <span className={`${styles.probabilityName} ${styles.probabilityNameAway}`}>
            {away.name}
          </span>
        </div>
        <div className={styles.probabilityTrack}>
          {labelled.map((segment) => (
            <span
              key={`bar-${segment.key}`}
              aria-hidden="true"
              className={`${styles.probabilitySegment} ${segment.fill}`}
              style={{ flexGrow: segment.share }}
            />
          ))}
        </div>
        {/* The labels sit against the bar instead of on it: small text on a
            coloured fill cannot keep a readable contrast. */}
        <div className={styles.probabilityLabels}>
          {labelled.map((segment) => (
            <span key={`label-${segment.key}`} className={styles.probabilityLabel}>
              <span
                aria-hidden="true"
                className={`${styles.probabilitySwatch} ${segment.fill}`}
              />
              {`${segment.key} ${formatPercent(segment.share)}`}
            </span>
          ))}
        </div>
        {scaled ? (
          <p className={styles.probabilityTotal}>
            {`The inputs add up to ${formatPercent(total)}; each segment shows its share of that total.`}
          </p>
        ) : null}
      </div>
    </ChartFigure>
  );
}
