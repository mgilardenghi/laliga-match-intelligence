import type { ReactElement, ReactNode } from "react";
import { ChartFigure } from "./ChartFigure";
import { chartIds } from "./chart-ids";
import { formatNumber } from "./format";
import { bandScale, linearScale } from "./scale";
import styles from "./charts.module.css";

export type SparklineHighlight = { readonly from: number; readonly to: number };

export type SparklineProps = {
  readonly metric: string;
  readonly seasons: readonly string[];
  /** One value per season; `null` marks a season with no value and breaks the line. */
  readonly values: readonly (number | null)[];
  /**
   * The segment to highlight. Omitted: the largest decline of the series is
   * highlighted. `null`: nothing is highlighted (the caller publishes no drop).
   */
  readonly highlight?: SparklineHighlight | null;
  readonly unit?: string;
  readonly id?: string;
  readonly title?: string;
  readonly description?: string;
  readonly note?: ReactNode;
};

// The mark overlay is a percent grid on both axes, so the path coordinates are
// shares of the plot and no coordinate depends on its rendered pixel size.
const PLOT_UNITS = 100;
// A value label sits above its dot unless the dot is this close to the top
// edge, where the label would leave the plot; there it moves below the dot.
const LABEL_FLIP_PERCENT = 25;
// The highlighted segment's label is kept this far from either edge, so a drop
// between the first two seasons cannot push the text out of the figure.
const HINT_LABEL_MIN_PERCENT = 12;
const HINT_LABEL_MAX_PERCENT = 88;

function isValue(value: number | null | undefined): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function clampPercent(value: number): number {
  if (!Number.isFinite(value)) {
    return 0;
  }
  return Math.min(PLOT_UNITS, Math.max(0, value));
}

/**
 * Keep a value label clear of the plot edge: the first label grows to the right
 * of its dot and the last one to the left, so neither runs past the plot.
 */
function valueLabelTransform(anchor: "start" | "end", top: number): string {
  const horizontal = anchor === "start" ? "0" : "-100%";
  const vertical = top < LABEL_FLIP_PERCENT ? "40%" : "-140%";
  return `translate(${horizontal}, ${vertical})`;
}

function largestDrop(values: readonly (number | null)[]): SparklineHighlight | undefined {
  if (values.length < 2) {
    return undefined;
  }
  let from = -1;
  let drop = 0;
  for (let index = 0; index < values.length - 1; index += 1) {
    const before = values[index];
    const after = values[index + 1];
    if (!isValue(before) || !isValue(after)) {
      continue;
    }
    const change = after - before;
    // Only a real decline is a drop: an increasing or flat series has none.
    if (change < drop) {
      drop = change;
      from = index;
    }
  }
  return from >= 0 ? { from, to: from + 1 } : undefined;
}

export function Sparkline(props: SparklineProps): ReactElement {
  const {
    metric,
    seasons,
    values,
    highlight,
    unit,
    id,
    description,
    note,
  } = props;
  const firstSeason = seasons[0] ?? "";
  const title = props.title ?? (firstSeason.length > 0 ? `${metric} since ${firstSeason}` : metric);
  const ids = chartIds(id ?? title);

  if (seasons.length !== values.length) {
    throw new Error(
      `Sparkline needs one value per season, but received ${seasons.length} seasons and ${values.length} values.`,
    );
  }

  const finiteValues = values.filter(isValue);
  const minimum = finiteValues.length > 0 ? Math.min(...finiteValues) : 0;
  const maximum = finiteValues.length > 0 ? Math.max(...finiteValues) : 1;
  const spread = maximum - minimum;
  const padding = spread === 0 ? Math.max(1, Math.abs(maximum) * 0.1) : spread * 0.15;
  // The y scale runs top to bottom so a value is a percent down from the top
  // edge, which is what the HTML dots and labels position by.
  const yScale = linearScale([minimum - padding, maximum + padding], [PLOT_UNITS, 0]);
  const xScale = bandScale(seasons.length, [0, PLOT_UNITS], 0.1);

  const selected = (() => {
    const candidate = highlight === undefined ? largestDrop(values) : highlight;
    if (candidate === undefined || candidate === null) {
      return undefined;
    }
    const low = Math.max(0, Math.min(candidate.from, candidate.to));
    const high = Math.min(seasons.length - 1, Math.max(candidate.from, candidate.to));
    return high > low ? { from: low, to: high } : undefined;
  })();
  const points = values.map((value, index) =>
    isValue(value)
      ? { x: clampPercent(xScale.center(index)), y: clampPercent(yScale.scale(value)) }
      : null,
  );
  // Runs of consecutive seasons with a value: a missing season breaks the line.
  const runs: { x: number; y: number }[][] = [];
  let current: { x: number; y: number }[] = [];
  for (const point of points) {
    if (point === null) {
      if (current.length > 0) {
        runs.push(current);
      }
      current = [];
    } else {
      current.push(point);
    }
  }
  if (current.length > 0) {
    runs.push(current);
  }
  const firstIndex = points.findIndex((point) => point !== null);
  let lastIndex = -1;
  for (let index = points.length - 1; index >= 0; index -= 1) {
    if (points[index] !== null) {
      lastIndex = index;
      break;
    }
  }
  const firstPoint = firstIndex >= 0 ? points[firstIndex] : null;
  const lastPoint = lastIndex > firstIndex ? points[lastIndex] : null;
  const fromPoint = selected === undefined ? null : points[selected.from];
  const toPoint = selected === undefined ? null : points[selected.to];
  // One condition decides both the drawing and the default description, so the
  // description cannot claim a segment that is absent. A null highlight, a
  // series without a decline, and a highlight that spans a season with no value
  // (values [null, 1] with highlight 0 -> 1) all leave the line unhighlighted,
  // because an endpoint has no point to draw from or to.
  const drawn = selected !== undefined && fromPoint !== null && toPoint !== null;
  const defaultDescription = drawn
    ? `Trend of ${metric} across ${seasons.length} seasons, with one season-to-season segment highlighted.`
    : `Trend of ${metric} across ${seasons.length} seasons.`;
  const hintLeft =
    drawn && selected !== undefined
      ? Math.min(
          HINT_LABEL_MAX_PERCENT,
          Math.max(HINT_LABEL_MIN_PERCENT, (fromPoint.x + toPoint.x) / 2),
        )
      : 0;

  return (
    <ChartFigure
      ids={ids}
      title={title}
      description={description ?? defaultDescription}
      table={{
        caption: title,
        columns: ["Season", "Value"],
        rows: seasons.map((season, index) => [
          season,
          isValue(values[index]) ? `${formatNumber(values[index] as number)}${unit ?? ""}` : "—",
        ]),
      }}
      note={note}
    >
      <div
        role="img"
        aria-labelledby={ids.labelledBy}
        className={`${styles.plot} ${styles.sparkline}`}
      >
        {drawn && selected !== undefined ? (
          <div className={styles.sparklineHint}>
            <span
              className={styles.sparklineHintLabel}
              style={{ left: `${hintLeft}%` }}
            >
              {`${seasons[selected.from]} → ${seasons[selected.to]}`}
            </span>
          </div>
        ) : null}
        <div className={styles.sparklinePlot}>
          <svg
            aria-hidden="true"
            focusable="false"
            viewBox={`0 0 ${PLOT_UNITS} ${PLOT_UNITS}`}
            preserveAspectRatio="none"
            className={styles.plotOverlay}
          >
            {runs
              .filter((run) => run.length > 1)
              .map((run, index) => (
                <polyline
                  key={`run-${index}`}
                  points={run.map((point) => `${point.x} ${point.y}`).join(" ")}
                  className={styles.sparklineTrend}
                  vectorEffect="non-scaling-stroke"
                />
              ))}
            {drawn ? (
              <line
                x1={fromPoint.x}
                y1={fromPoint.y}
                x2={toPoint.x}
                y2={toPoint.y}
                className={styles.sparklineDrop}
                vectorEffect="non-scaling-stroke"
              />
            ) : null}
          </svg>
          {points.map((point, index) =>
            point === null ? null : (
              <span
                key={`point-${index}`}
                aria-hidden="true"
                className={styles.sparklineDot}
                style={{ left: `${point.x}%`, top: `${point.y}%` }}
              />
            ),
          )}
          {firstPoint !== null ? (
            <span
              className={styles.sparklineValue}
              style={{
                left: `${firstPoint.x}%`,
                top: `${firstPoint.y}%`,
                transform: valueLabelTransform("start", firstPoint.y),
              }}
            >
              {`${formatNumber(values[firstIndex] as number)}${unit ?? ""}`}
            </span>
          ) : null}
          {lastPoint !== null ? (
            <span
              className={styles.sparklineValue}
              style={{
                left: `${lastPoint.x}%`,
                top: `${lastPoint.y}%`,
                transform: valueLabelTransform("end", lastPoint.y),
              }}
            >
              {`${formatNumber(values[lastIndex] as number)}${unit ?? ""}`}
            </span>
          ) : null}
        </div>
        <div className={styles.sparklineSeasons}>
          {seasons.length > 0 ? <span>{seasons[0]}</span> : null}
          {seasons.length > 1 ? <span>{seasons[seasons.length - 1]}</span> : null}
        </div>
      </div>
    </ChartFigure>
  );
}
