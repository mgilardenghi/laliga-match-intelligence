import type { ReactElement, ReactNode } from "react";
import { ChartFigure } from "./ChartFigure";
import { chartIds } from "./chart-ids";
import { formatPoints } from "./format";
import { linearScale, niceMax } from "./scale";
import styles from "./charts.module.css";

export type IntervalBarRow = {
  readonly team: string;
  readonly p05: number;
  readonly p25: number;
  readonly p50: number;
  readonly p75: number;
  readonly p95: number;
  readonly current: number;
};

export type IntervalBarsProps = {
  readonly rows: readonly IntervalBarRow[];
  readonly threshold?: number;
  readonly thresholdLabel?: string;
  readonly id?: string;
  readonly title?: string;
  readonly description?: string;
  readonly note?: ReactNode;
};

const DEFAULT_TITLE = "Projected final points";
const DEFAULT_DESCRIPTION =
  "Projected final points per team: the thin bar spans the 5th to 95th percentile, the thick bar the interquartile range, the tick marks the median and the diamond marks current points.";

const WIDTH = 480;
const PLOT_LEFT = 104;
const PLOT_RIGHT = 468;
const TOP = 26;
const ROW_HEIGHT = 26;
const BOTTOM = 24;

export function IntervalBars(props: IntervalBarsProps): ReactElement {
  const {
    rows,
    threshold,
    thresholdLabel = "Survival threshold",
    id,
    title = DEFAULT_TITLE,
    description = DEFAULT_DESCRIPTION,
    note,
  } = props;
  const ids = chartIds(id ?? title);

  const height = TOP + Math.max(1, rows.length) * ROW_HEIGHT + BOTTOM;
  const maximum = rows.reduce((max, row) => {
    const lowest = Math.min(row.p05, row.current);
    const highest = Math.max(row.p95, row.current, row.p50, row.p75);
    return Math.max(max, highest, -lowest, threshold ?? 0);
  }, 0);
  const scale = linearScale([0, niceMax(maximum)], [PLOT_LEFT, PLOT_RIGHT]);
  const ticks = scale.ticks(5);

  return (
    <ChartFigure
      title={title}
      description={description}
      table={{
        caption: "Projected final points",
        columns: ["Team", "p05", "p25", "p50", "p75", "p95", "Current points"],
        rows: rows.map((row) => [
          row.team,
          formatPoints(row.p05),
          formatPoints(row.p25),
          formatPoints(row.p50),
          formatPoints(row.p75),
          formatPoints(row.p95),
          formatPoints(row.current),
        ]),
      }}
      note={note}
    >
      <svg
        role="img"
        aria-labelledby={ids.labelledBy}
        viewBox={`0 0 ${WIDTH} ${height}`}
        className={styles.svg}
        preserveAspectRatio="xMidYMid meet"
      >
        <title id={ids.titleId}>{title}</title>
        <desc id={ids.descId}>{description}</desc>
        {ticks.map((tick) => {
          const x = scale.scale(tick);
          return (
            <g key={`tick-${tick}`}>
              <line x1={x} y1={TOP - 8} x2={x} y2={height - BOTTOM + 4} className={styles.gridLine} />
              <text x={x} y={height - 8} textAnchor="middle" className={styles.axisText}>
                {formatPoints(tick)}
              </text>
            </g>
          );
        })}
        <line
          x1={PLOT_LEFT}
          y1={height - BOTTOM + 4}
          x2={PLOT_RIGHT}
          y2={height - BOTTOM + 4}
          className={styles.axisLine}
        />
        {rows.map((row, index) => {
          const centreY = TOP + index * ROW_HEIGHT + ROW_HEIGHT / 2;
          const p05 = scale.scale(Math.min(row.p05, row.p95));
          const p95 = scale.scale(Math.max(row.p05, row.p95));
          const p25 = scale.scale(Math.min(row.p25, row.p75));
          const p75 = scale.scale(Math.max(row.p25, row.p75));
          const median = scale.scale(row.p50);
          const current = scale.scale(row.current);
          return (
            <g key={row.team}>
              <text
                x={PLOT_LEFT - 8}
                y={centreY}
                textAnchor="end"
                dominantBaseline="central"
                className={styles.segmentName}
              >
                {row.team}
              </text>
              <rect
                x={p05}
                y={centreY - 3}
                width={Math.max(0, p95 - p05)}
                height={6}
                rx={3}
                style={{ fill: "var(--muted, #6b7280)" }}
              />
              <rect
                x={p25}
                y={centreY - 7}
                width={Math.max(0, p75 - p25)}
                height={14}
                rx={3}
                style={{ fill: "var(--accent, #0072b2)", opacity: 0.9 }}
              />
              <line
                x1={median}
                y1={centreY - 9}
                x2={median}
                y2={centreY + 9}
                style={{ stroke: "currentColor", strokeWidth: 2 }}
              />
              <polygon
                points={`${current},${centreY - 5} ${current + 5},${centreY} ${current},${centreY + 5} ${current - 5},${centreY}`}
                className={styles.currentMarker}
              />
            </g>
          );
        })}
        {threshold !== undefined ? (
          <g>
            <line
              x1={scale.scale(threshold)}
              y1={TOP - 12}
              x2={scale.scale(threshold)}
              y2={height - BOTTOM + 4}
              style={{
                stroke: "var(--zone-relegation, #d55e00)",
                strokeWidth: 1,
                strokeDasharray: "4 3",
              }}
            />
            <text
              x={scale.scale(threshold) + (scale.scale(threshold) > PLOT_RIGHT - 90 ? -4 : 4)}
              y={12}
              textAnchor={scale.scale(threshold) > PLOT_RIGHT - 90 ? "end" : "start"}
              className={styles.axisText}
              style={{ fill: "currentColor", opacity: 1 }}
            >
              {thresholdLabel}
            </text>
          </g>
        ) : null}
      </svg>
    </ChartFigure>
  );
}
