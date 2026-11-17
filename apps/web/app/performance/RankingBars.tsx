import type { ReactElement } from "react";

import { ChartFigure } from "@/components/charts";
import { formatValue } from "@/lib/format";

import styles from "./ranking.module.css";

export type RankingBarRow = {
  /** The display label of the model. */
  readonly name: string;
  /** The role line under the name, from `kindLine`. */
  readonly kind: string;
  readonly rps: number;
  readonly offScale: boolean;
};

export type RankingBarsProps = {
  readonly title: string;
  /** The rows to rank, already sorted by RPS ascending. */
  readonly rows: readonly RankingBarRow[];
  /** The axis ends the in-scale bars are measured against. */
  readonly domain: readonly [number, number];
};

/**
 * The share of the track a bar fills, in percent.
 *
 * The bar is clamped to two percent so a value on the lower end of the axis
 * still shows a mark, and to the whole track so no bar paints past its row.
 */
function barWidth(
  rps: number,
  domain: readonly [number, number],
): number {
  const [lo, hi] = domain;
  const span = hi - lo;
  if (!Number.isFinite(span) || span <= 0) {
    return 100;
  }
  return Math.min(100, Math.max(2, ((rps - lo) / span) * 100));
}

/** One sentence naming the axis end and every entry that misses the scale. */
function axisNote(
  domain: readonly [number, number],
  offScale: readonly RankingBarRow[],
): string {
  const opening = `The axis starts at ${formatValue(domain[0], 3)} so the gaps between models are visible.`;
  if (offScale.length === 0) {
    return opening;
  }
  const entries = offScale
    .map((row) => `${row.name} (${formatValue(row.rps, 3)})`)
    .join(", ");
  return `The axis starts at ${formatValue(domain[0], 3)} so the gaps between models are visible; ${entries} ${
    offScale.length === 1 ? "sits" : "sit"
  } off the scale.`;
}

/**
 * The model ranking as a list of bars.
 *
 * The bars are HTML, not SVG marks, so every row keeps its name, its role and
 * its score in the reading order and in the accessibility tree: the filled bar
 * itself is decoration and is hidden, while the value it draws is printed next
 * to it. The figure's data table repeats the same three columns for the same
 * reason.
 */
export function RankingBars(props: RankingBarsProps): ReactElement {
  const { title, rows, domain } = props;
  const [lo, hi] = domain;
  const offScale = rows.filter((row) => row.offScale);
  const middle = (lo + hi) / 2;

  return (
    <ChartFigure
      title={title}
      description="Ranked probability score of every model on the same matches, lower is better. Each bar starts at the lower end of the axis; a score past the axis is marked off scale, and its value is printed next to it."
      table={{
        caption: title,
        columns: ["Model", "Kind", "RPS"],
        rows: rows.map((row) => [row.name, row.kind, formatValue(row.rps, 3)]),
      }}
      note={axisNote(domain, offScale)}
    >
      <p className={styles.axis}>
        <span className={styles.axisEnd}>{formatValue(lo, 3)} better</span>
        <span className={styles.axisMiddle}>{formatValue(middle, 3)}</span>
        <span className={styles.axisEnd}>{formatValue(hi, 3)} worse</span>
      </p>
      <ol className={styles.list}>
        {rows.map((row, index) => (
          <li className={styles.row} key={`${row.name}-${index}`}>
            <span className={styles.model}>
              <strong className={styles.name}>{row.name}</strong>
              <span className={styles.kind}>{row.kind}</span>
            </span>
            <span className={styles.plot}>
              <span className={styles.track}>
                <span
                  className={styles.bar}
                  aria-hidden="true"
                  style={{
                    width: `${row.offScale ? 100 : barWidth(row.rps, domain)}%`,
                  }}
                />
              </span>
              {row.offScale ? (
                <span className={styles.offScale}>off scale</span>
              ) : null}
            </span>
            <span className={styles.value}>{formatValue(row.rps, 3)}</span>
          </li>
        ))}
      </ol>
    </ChartFigure>
  );
}
