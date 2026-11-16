import type { ReactElement, ReactNode } from "react";
import { ChartFigure } from "./ChartFigure";
import { chartIds } from "./chart-ids";
import { formatNumber } from "./format";
import { linearScale } from "./scale";
import styles from "./charts.module.css";

export type DotPlotReference = {
  readonly metric: string;
  readonly label: string;
  readonly value: number;
  /**
   * Leave this reference out of the x domain it would otherwise widen and mark
   * it at the panel edge instead, with its label and its value spelled out in
   * HTML. A reference whose value still falls inside the domain is drawn in
   * place, because a marker at the edge would misplace it.
   */
  readonly offScale?: boolean;
};

export type DotPlotProps = {
  readonly metrics: readonly string[];
  readonly rows: readonly {
    readonly model: string;
    readonly values: readonly (number | null)[];
  }[];
  readonly references?: readonly DotPlotReference[];
  /**
   * An explicit x domain per metric name. A metric without an entry keeps the
   * fitted data-extent domain.
   */
  readonly domains?: Readonly<Record<string, readonly [number, number]>>;
  readonly id?: string;
  readonly title?: string;
  readonly description?: string;
  readonly note?: ReactNode;
};

const DEFAULT_TITLE = "Model metrics compared";
const DEFAULT_DESCRIPTION =
  "One panel per metric, with one row per model. Filled dots show each model's value; dashed lines mark reference values.";

const TICK_COUNT = 4;
// The plot column is measured in percent, so every mark position is a share of
// it and no coordinate depends on the rendered pixel width.
const PLOT_UNITS = 100;
// Reference labels sit on two rows so two references with nearly the same value
// cannot overwrite each other.
const LABEL_ROWS = 2;

// The dot colour for each model comes from a static class map, so the palette
// stays in the stylesheet and no colour is written inline.
const MODEL_CLASSES: readonly string[] = [
  styles.dotCat1,
  styles.dotCat2,
  styles.dotCat3,
  styles.dotCat4,
];
const MUTED_CLASS = styles.dotMuted;

type PlacedReference = DotPlotReference & {
  readonly x: number;
  readonly row: number;
};

function clampPercent(value: number): number {
  if (!Number.isFinite(value)) {
    return 0;
  }
  return Math.min(100, Math.max(0, value));
}

function isValue(value: number | null | undefined): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

/**
 * The room a reference label needs on either side of its line, in percent of
 * the plot column: 12 px text is about half an em per character, and the column
 * is a few hundred pixels wide.
 */
function labelHalfWidth(label: string): number {
  return Math.min(30, 1.1 + label.length * 1.1);
}

/**
 * Place reference labels on the lowest row that still has room, so labels of
 * two references close together stay readable.
 */
function placeReferences(
  references: readonly DotPlotReference[],
  position: (value: number) => number,
): readonly PlacedReference[] {
  const ordered = [...references].sort((left, right) => left.value - right.value);
  const occupied: { low: number; high: number }[][] = Array.from(
    { length: LABEL_ROWS },
    () => [],
  );
  const placed: PlacedReference[] = [];
  for (const reference of ordered) {
    const x = position(reference.value);
    const half = labelHalfWidth(reference.label);
    const low = x - half;
    const high = x + half;
    const row = occupied.findIndex((spans) =>
      spans.every((span) => high <= span.low || low >= span.high),
    );
    const rowIndex = row >= 0 ? row : LABEL_ROWS - 1;
    occupied[rowIndex].push({ low, high });
    placed.push({ ...reference, x, row: rowIndex });
  }
  return placed;
}

/**
 * The x domain of one metric: the explicit one when the caller gave a usable
 * domain, otherwise the data extent with the padding a dot plot needs. A dot
 * plot has no zero baseline to preserve, so the axis spans the data instead of
 * starting at zero: otherwise every model collapses onto one x position and the
 * chart says nothing.
 */
function resolveDomain(
  values: readonly number[],
  explicit: readonly [number, number] | undefined,
): readonly [number, number] {
  if (
    explicit !== undefined &&
    Number.isFinite(explicit[0]) &&
    Number.isFinite(explicit[1]) &&
    explicit[0] < explicit[1]
  ) {
    return explicit;
  }
  const minimum = values.length > 0 ? Math.min(...values) : 0;
  const maximum = values.length > 0 ? Math.max(...values) : 1;
  const span = maximum - minimum;
  const padding = span > 0 ? span * 0.08 : Math.max(0.05, Math.abs(maximum) * 0.1);
  return [minimum - padding, maximum + padding];
}

export function DotPlot(props: DotPlotProps): ReactElement {
  const {
    metrics,
    rows,
    references,
    domains,
    id,
    title = DEFAULT_TITLE,
    description = DEFAULT_DESCRIPTION,
    note,
  } = props;
  const ids = chartIds(id ?? title);
  const allReferences = references ?? [];

  return (
    <ChartFigure
      ids={ids}
      title={title}
      description={description}
      table={{
        caption: "Model metric values",
        columns: ["Metric", "Model", "Value"],
        rows: metrics.flatMap((metric, metricIndex) =>
          rows.map((row) => {
            const value = row.values[metricIndex];
            return [
              metric,
              row.model,
              isValue(value) ? formatNumber(value, 3) : "—",
            ];
          }),
        ),
      }}
      note={note}
    >
      <div
        role="img"
        aria-labelledby={ids.labelledBy}
        className={`${styles.plot} ${styles.dotChart}`}
      >
        {metrics.map((metric, metricIndex) => {
          const modelValues = rows
            .map((row) => row.values[metricIndex])
            .filter((value): value is number => isValue(value));
          // A reference without a number has no place on a numeric axis.
          const metricReferences = allReferences.filter(
            (reference) => reference.metric === metric && Number.isFinite(reference.value),
          );
          // An off-scale reference never widens the domain it is off.
          const inScaleValues = metricReferences
            .filter((reference) => reference.offScale !== true)
            .map((reference) => reference.value);
          const domain = resolveDomain([...modelValues, ...inScaleValues], domains?.[metric]);
          const axis = linearScale(domain, [0, PLOT_UNITS]);
          const position = (value: number): number => clampPercent(axis.scale(value));
          const ticks = axis.ticks(TICK_COUNT);
          const inside = metricReferences.filter(
            (reference) => reference.value >= domain[0] && reference.value <= domain[1],
          );
          const outside = metricReferences.filter(
            (reference) => reference.value < domain[0] || reference.value > domain[1],
          );
          const placed = placeReferences(inside, position);

          return (
            <div key={metric} className={styles.dotPanel}>
              <p className={styles.panelTitle}>{metric}</p>
              {placed.length > 0 ? (
                <div className={styles.referenceRow}>
                  <span aria-hidden="true" />
                  <div className={styles.referenceTrack}>
                    {placed.map((reference) => (
                      <span
                        key={`${metric}-label-${reference.label}`}
                        className={styles.referenceLabel}
                        style={{
                          left: `${reference.x}%`,
                          bottom: `${reference.row * 0.95}rem`,
                        }}
                      >
                        {reference.label}
                      </span>
                    ))}
                  </div>
                  <span aria-hidden="true" />
                </div>
              ) : null}
              <div className={styles.dotRows}>
                {rows.map((row, rowIndex) => {
                  const value = row.values[metricIndex];
                  const colourClass = MODEL_CLASSES[rowIndex] ?? MUTED_CLASS;
                  return (
                    <div key={`${metric}-${row.model}`} className={styles.dotRow}>
                      <span className={styles.chartLabel}>{row.model}</span>
                      <div className={styles.dotTrack}>
                        {isValue(value) ? (
                          <span
                            className={`${styles.dotMark} ${colourClass}`}
                            style={{ left: `${position(value)}%` }}
                          />
                        ) : null}
                      </div>
                      <span className={styles.chartValue}>
                        {isValue(value) ? formatNumber(value, 3) : "—"}
                      </span>
                    </div>
                  );
                })}
                <div className={styles.plotLayer}>
                  <svg
                    aria-hidden="true"
                    focusable="false"
                    viewBox={`0 0 ${PLOT_UNITS} ${PLOT_UNITS}`}
                    preserveAspectRatio="none"
                    className={styles.plotOverlay}
                  >
                    {ticks.map((tick) => (
                      <line
                        key={`${metric}-grid-${tick}`}
                        x1={position(tick)}
                        x2={position(tick)}
                        y1={0}
                        y2={PLOT_UNITS}
                        className={styles.plotGridLine}
                        vectorEffect="non-scaling-stroke"
                      />
                    ))}
                    <line
                      x1={0}
                      x2={PLOT_UNITS}
                      y1={PLOT_UNITS}
                      y2={PLOT_UNITS}
                      className={styles.plotAxisLine}
                      vectorEffect="non-scaling-stroke"
                    />
                  </svg>
                  {placed.map((reference) => (
                    <span
                      key={`${metric}-line-${reference.label}`}
                      className={styles.referenceLine}
                      style={{ left: `${reference.x}%` }}
                    />
                  ))}
                </div>
              </div>
              <div className={styles.tickRow}>
                <span aria-hidden="true" />
                <div className={styles.tickTrack}>
                  {ticks.map((tick) => (
                    <span
                      key={`${metric}-tick-${tick}`}
                      className={styles.tickLabel}
                      style={{ left: `${position(tick)}%` }}
                    >
                      {formatNumber(tick, Math.abs(tick) < 10 ? 2 : 0)}
                    </span>
                  ))}
                </div>
                <span aria-hidden="true" />
              </div>
              {outside.map((reference) => {
                const above = reference.value > domain[1];
                return (
                  <div key={`${metric}-off-${reference.label}`} className={styles.offScaleRow}>
                    <span aria-hidden="true" />
                    <div className={styles.offScaleTrack}>
                      <span
                        aria-hidden="true"
                        className={
                          above ? styles.offScaleMarkerHigh : styles.offScaleMarkerLow
                        }
                      />
                      <p className={styles.offScaleNote}>
                        {`${reference.label} ${formatNumber(reference.value, 3)}`}{" "}
                        <span className={styles.offScaleTag}>
                          {above ? "above the axis" : "below the axis"}
                        </span>
                      </p>
                    </div>
                    <span aria-hidden="true" />
                  </div>
                );
              })}
            </div>
          );
        })}
      </div>
    </ChartFigure>
  );
}
