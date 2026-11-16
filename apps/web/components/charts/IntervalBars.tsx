import type { ReactElement, ReactNode } from "react";
import { ChartFigure } from "./ChartFigure";
import { chartIds } from "./chart-ids";
import { formatPoints } from "./format";
import { fittedDomain, linearScale } from "./scale";
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

const TICK_COUNT = 5;
// The mark overlay uses percent horizontally and one hundred units per row
// vertically, so its coordinates are independent of the rendered pixel size.
const PLOT_UNITS = 100;
const MEDIAN_HALF_UNITS = 32;

function clampPercent(value: number): number {
  if (!Number.isFinite(value)) {
    return 0;
  }
  return Math.min(100, Math.max(0, value));
}

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

  // The axis fits the data, including the threshold when one is drawn, so the
  // last tick is the axis end and the end is never further than one step from
  // the largest value.
  const values: number[] = [];
  for (const row of rows) {
    values.push(row.p05, row.p25, row.p50, row.p75, row.p95, row.current);
  }
  if (threshold !== undefined) {
    values.push(threshold);
  }
  const { domain, ticks } = fittedDomain(values, TICK_COUNT);
  const axis = linearScale(domain, [0, 100]);
  const position = (value: number): number => clampPercent(axis.scale(value));

  const rowCount = Math.max(1, rows.length);
  const plotHeight = rowCount * PLOT_UNITS;

  return (
    <ChartFigure
      ids={ids}
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
      <div className={`${styles.plot} ${styles.intervalChart}`}>
        {threshold !== undefined ? (
          <div className={styles.thresholdRow}>
            <span aria-hidden="true" />
            <div className={styles.thresholdTrack}>
              <span
                className={styles.thresholdLabel}
                style={{ left: `${position(threshold)}%` }}
              >
                {thresholdLabel}
              </span>
            </div>
            <span aria-hidden="true" />
          </div>
        ) : null}
        <div className={styles.rows}>
          {rows.map((row) => {
            const low = position(Math.min(row.p05, row.p95));
            const high = position(Math.max(row.p05, row.p95));
            const lowerQuartile = position(Math.min(row.p25, row.p75));
            const upperQuartile = position(Math.max(row.p25, row.p75));
            return (
              <div key={row.team} className={styles.row}>
                <span className={styles.chartLabel}>{row.team}</span>
                <div className={styles.barTrack}>
                  <span
                    className={styles.barRange}
                    style={{ left: `${low}%`, width: `${Math.max(0, high - low)}%` }}
                  />
                  <span
                    className={styles.barMiddle}
                    style={{
                      left: `${lowerQuartile}%`,
                      width: `${Math.max(0, upperQuartile - lowerQuartile)}%`,
                    }}
                  />
                  <span
                    className={styles.barCurrent}
                    style={{ left: `${position(row.current)}%` }}
                  />
                </div>
                <span className={styles.chartValue}>{formatPoints(row.p50)}</span>
              </div>
            );
          })}
          <div className={styles.plotLayer}>
            <svg
              role="img"
              aria-labelledby={ids.labelledBy}
              viewBox={`0 0 ${PLOT_UNITS} ${plotHeight}`}
              preserveAspectRatio="none"
              className={styles.plotOverlay}
            >
              {ticks.map((tick) => (
                <line
                  key={`grid-${tick}`}
                  x1={position(tick)}
                  x2={position(tick)}
                  y1={0}
                  y2={plotHeight}
                  className={styles.plotGridLine}
                  vectorEffect="non-scaling-stroke"
                />
              ))}
              <line
                x1={0}
                x2={PLOT_UNITS}
                y1={plotHeight}
                y2={plotHeight}
                className={styles.plotAxisLine}
                vectorEffect="non-scaling-stroke"
              />
              {rows.map((row, index) => {
                const centre = index * PLOT_UNITS + PLOT_UNITS / 2;
                return (
                  <line
                    key={`median-${row.team}`}
                    x1={position(row.p50)}
                    x2={position(row.p50)}
                    y1={centre - MEDIAN_HALF_UNITS}
                    y2={centre + MEDIAN_HALF_UNITS}
                    className={styles.medianTick}
                    vectorEffect="non-scaling-stroke"
                  />
                );
              })}
              {threshold !== undefined ? (
                <line
                  data-chart-threshold=""
                  x1={position(threshold)}
                  x2={position(threshold)}
                  y1={0}
                  y2={plotHeight}
                  className={styles.thresholdLine}
                  vectorEffect="non-scaling-stroke"
                />
              ) : null}
            </svg>
          </div>
        </div>
        <div className={styles.tickRow}>
          <span aria-hidden="true" />
          <div className={styles.tickTrack}>
            {ticks.map((tick) => (
              <span
                key={`label-${tick}`}
                data-chart-tick={tick}
                className={styles.tickLabel}
                style={{ left: `${position(tick)}%` }}
              >
                {formatPoints(tick)}
              </span>
            ))}
          </div>
          <span aria-hidden="true" />
        </div>
      </div>
    </ChartFigure>
  );
}
