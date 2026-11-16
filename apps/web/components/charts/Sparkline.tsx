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

const WIDTH = 480;
const PLOT_LEFT = 16;
const PLOT_RIGHT = 464;
const PLOT_TOP = 30;
const PLOT_BOTTOM = 118;
const HEIGHT = 146;

function isValue(value: number | null | undefined): value is number {
  return typeof value === "number" && Number.isFinite(value);
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
  const defaultDescription = `Trend of ${metric} across ${seasons.length} seasons, with one season-to-season segment highlighted.`;
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
  const yScale = linearScale([minimum - padding, maximum + padding], [PLOT_BOTTOM, PLOT_TOP]);
  const xScale = bandScale(seasons.length, [PLOT_LEFT, PLOT_RIGHT], 0.1);

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
    isValue(value) ? { x: xScale.center(index), y: yScale.scale(value) } : null,
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

  return (
    <ChartFigure
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
      <svg
        role="img"
        aria-labelledby={ids.labelledBy}
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        className={styles.svg}
        preserveAspectRatio="xMidYMid meet"
      >
        <title id={ids.titleId}>{title}</title>
        <desc id={ids.descId}>{description ?? defaultDescription}</desc>
        {runs
          .filter((run) => run.length > 1)
          .map((run, index) => (
            <polyline
              key={`run-${index}`}
              points={run.map((point) => `${point.x} ${point.y}`).join(" ")}
              fill="none"
              strokeLinecap="round"
              strokeLinejoin="round"
              style={{ stroke: "var(--accent, #0072b2)", strokeWidth: 1.6 }}
            />
          ))}
        {points.map((point, index) =>
          point === null ? null : (
            <circle
              key={`point-${index}`}
              cx={point.x}
              cy={point.y}
              r={2.2}
              style={{ fill: "var(--accent, #0072b2)" }}
            />
          ),
        )}
        {selected !== undefined && fromPoint != null && toPoint != null ? (
          <g>
            <line
              x1={fromPoint.x}
              y1={fromPoint.y}
              x2={toPoint.x}
              y2={toPoint.y}
              strokeLinecap="round"
              style={{ stroke: "var(--zone-relegation, #d55e00)", strokeWidth: 3.4 }}
            />
            <text
              x={(fromPoint.x + toPoint.x) / 2}
              y={14}
              textAnchor="middle"
              className={styles.axisText}
              style={{ fill: "currentColor", opacity: 1 }}
            >
              {`${seasons[selected.from]} → ${seasons[selected.to]}`}
            </text>
          </g>
        ) : null}
        {firstPoint !== null ? (
          <text
            x={firstPoint.x}
            y={Math.max(PLOT_TOP - 4, firstPoint.y - 7)}
            textAnchor="start"
            className={styles.endLabel}
          >
            {`${formatNumber(values[firstIndex] as number)}${unit ?? ""}`}
          </text>
        ) : null}
        {lastPoint !== null ? (
          <text
            x={lastPoint.x}
            y={Math.min(PLOT_BOTTOM - 2, lastPoint.y + 13)}
            textAnchor="end"
            className={styles.endLabel}
          >
            {`${formatNumber(values[lastIndex] as number)}${unit ?? ""}`}
          </text>
        ) : null}
        {seasons.length > 0 ? (
          <text x={PLOT_LEFT} y={PLOT_BOTTOM + 16} textAnchor="start" className={styles.axisText}>
            {seasons[0]}
          </text>
        ) : null}
        {seasons.length > 1 ? (
          <text x={PLOT_RIGHT} y={PLOT_BOTTOM + 16} textAnchor="end" className={styles.axisText}>
            {seasons[seasons.length - 1]}
          </text>
        ) : null}
      </svg>
    </ChartFigure>
  );
}
