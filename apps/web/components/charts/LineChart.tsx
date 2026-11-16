import type { ReactElement, ReactNode } from "react";
import { ChartFigure } from "./ChartFigure";
import { chartIds } from "./chart-ids";
import { formatNumber } from "./format";
import { bandScale, linearScale, niceMax } from "./scale";
import styles from "./charts.module.css";

export type LineSeries = {
  readonly name: string;
  readonly values: readonly (number | null)[];
  readonly emphasis?: boolean;
  /**
   * Leave this series out of the y domain and draw no path for it: it becomes a
   * marker at the plot edge nearest its value (top when above the domain,
   * bottom when below it) and its last finite value prints in the gutter, so the
   * series is never silently dropped.
   */
  readonly offScale?: boolean;
};

export type LineChartProps = {
  readonly xLabels: readonly string[];
  readonly series: readonly LineSeries[];
  readonly yLabel?: string;
  readonly valueSuffix?: string;
  /** An explicit y domain instead of the data-derived one. */
  readonly domain?: readonly [number, number];
  readonly id?: string;
  readonly title: string;
  readonly description: string;
  readonly note?: ReactNode;
};

// The mark overlay is a percent grid on both axes, so the path coordinates are
// shares of the plot and no coordinate depends on its rendered pixel size.
const PLOT_UNITS = 100;
// The gutter keeps its labels clear of the plot; the plot itself keeps a
// readable height and never stretches to the height of a tall page column.
const GUTTER_LABEL_MIN_GAP = 9;
const MAX_SERIES = 5;

const SERIES_COLOURS: readonly string[] = [
  // Every fallback keeps at least 3:1 against both the light and the dark page
  // background, so a chart is legible before the theme tokens exist.
  "var(--cat-1, #b45309)",
  "var(--cat-2, #0f766e)",
  "var(--cat-3, #cc79a7)",
  "var(--cat-4, #33608c)",
];

const MUTED_COLOUR = "var(--muted, #6b7280)";
const ACCENT_COLOUR = "var(--accent, #0072b2)";

type EndLabel = {
  readonly name: string;
  readonly value: number;
  readonly colour: string;
  /** Vertical position in percent of the plot height. */
  top: number;
};

type OffScaleEntry = {
  readonly name: string;
  readonly value: number;
  readonly colour: string;
  readonly left: number;
  /** Whether the value sits above the domain, which decides its edge. */
  readonly above: boolean;
};

function isValue(value: number | null | undefined): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function clampPercent(value: number): number {
  if (!Number.isFinite(value)) {
    return 0;
  }
  return Math.min(100, Math.max(0, value));
}

function round(value: number): number {
  return Number(value.toFixed(3));
}

/** The last value the series publishes, searching back over trailing gaps. */
function lastFinite(values: readonly (number | null)[]): { readonly value: number; readonly index: number } | undefined {
  for (let index = values.length - 1; index >= 0; index -= 1) {
    const value = values[index];
    if (isValue(value)) {
      return { value, index };
    }
  }
  return undefined;
}

export function LineChart(props: LineChartProps): ReactElement {
  const {
    xLabels,
    series,
    yLabel,
    valueSuffix,
    domain,
    id,
    title,
    description,
    note,
  } = props;

  if (series.length > MAX_SERIES) {
    throw new Error(
      `LineChart accepts at most ${MAX_SERIES} series, but received ${series.length}. Split the chart or keep the five most relevant series.`,
    );
  }

  const ids = chartIds(id ?? title);

  // An off-scale series never widens the axis it is off.
  const plotted = series.filter((item) => item.offScale !== true);
  const numeric = plotted.flatMap((item) => item.values.filter(isValue));
  const rawMax = numeric.length > 0 ? Math.max(...numeric) : 1;
  const rawMin = numeric.length > 0 ? Math.min(...numeric) : 0;

  const explicitDomain =
    domain !== undefined &&
    Number.isFinite(domain[0]) &&
    Number.isFinite(domain[1]) &&
    domain[0] < domain[1];
  const yMin = explicitDomain && domain !== undefined ? domain[0] : rawMin < 0 ? -niceMax(-rawMin) : 0;
  const yMax =
    explicitDomain && domain !== undefined
      ? domain[1]
      : Math.max(yMin + 1, rawMax > 0 ? niceMax(rawMax) : 1);

  // The y scale runs top to bottom so a value is a percent down from the top
  // edge, which is what the HTML labels and the mark overlay both position by.
  const yScale = linearScale([yMin, yMax], [PLOT_UNITS, 0]);
  const xScale = bandScale(xLabels.length, [0, PLOT_UNITS], 0.15);
  const yTicks = yScale.ticks(4);

  // The emphasised series takes the accent; the others take the categorical
  // palette in the order they appear, so no counter has to be threaded through
  // the render.
  const categoricalOrder = series
    .map((item, index) => ({ index, emphasised: item.emphasis === true }))
    .filter((entry) => !entry.emphasised)
    .map((entry) => entry.index);
  const colours = series.map((item, index) => {
    if (item.emphasis === true) {
      return ACCENT_COLOUR;
    }
    return SERIES_COLOURS[categoricalOrder.indexOf(index)] ?? MUTED_COLOUR;
  });

  const xTickStep = Math.max(1, Math.ceil(xLabels.length / 6));
  const unitText = [yLabel, valueSuffix !== undefined ? `(${valueSuffix})` : undefined]
    .filter((part): part is string => part !== undefined && part.length > 0)
    .join(" ");

  const endLabels: EndLabel[] = [];
  // A path reads its colour by the index of the series it belongs to, not by
  // the index of the drawn paths, so an off-scale series cannot shift it.
  const paths = series
    .map((item, seriesIndex) => ({ item, seriesIndex }))
    .filter(({ item }) => item.offScale !== true);
  const offScaleEntries: OffScaleEntry[] = [];
  series.forEach((item, seriesIndex) => {
    const last = lastFinite(item.values);
    if (last === undefined) {
      return;
    }
    const colour = colours[seriesIndex];
    const index = Math.min(last.index, xLabels.length - 1);
    if (item.offScale === true) {
      offScaleEntries.push({
        name: item.name,
        value: last.value,
        colour,
        left: xScale.center(Math.max(0, index)),
        // A value that is not below the domain keeps the top edge the marker
        // has always used, so an off-scale series never changes words or place
        // by a rounding step.
        above: !(last.value < yMin),
      });
      return;
    }
    endLabels.push({
      name: item.name,
      value: last.value,
      colour,
      top: clampPercent(yScale.scale(last.value)),
    });
  });

  // Labels are stacked apart far enough to stay legible, then the whole column
  // slides up when it would run past the bottom of the plot.
  endLabels.sort((left, right) => left.top - right.top);
  endLabels.forEach((label, index) => {
    const minimum = index === 0 ? 0 : endLabels[index - 1].top + GUTTER_LABEL_MIN_GAP;
    label.top = clampPercent(Math.max(label.top, minimum));
  });
  const overflow =
    endLabels.length > 0 ? endLabels[endLabels.length - 1].top - PLOT_UNITS : 0;
  if (overflow > 0) {
    for (const label of endLabels) {
      label.top = clampPercent(label.top - overflow);
    }
  }

  return (
    <ChartFigure
      ids={ids}
      title={title}
      description={description}
      table={{
        caption: title,
        columns: ["x", ...series.map((item) => item.name)],
        rows: xLabels.map((label, index) => [
          label,
          ...series.map((item) => {
            const value = item.values[index];
            if (!isValue(value)) {
              return "—";
            }
            return `${formatNumber(value)}${valueSuffix ?? ""}`;
          }),
        ]),
      }}
      note={note}
    >
      <div
        role="img"
        aria-labelledby={ids.labelledBy}
        className={`${styles.plot} ${styles.lineChart}`}
      >
        {unitText.length > 0 ? (
          <div className={styles.lineUnitRow}>
            <span aria-hidden="true" />
            <span className={styles.lineUnit}>{unitText}</span>
            <span aria-hidden="true" />
          </div>
        ) : null}
        {offScaleEntries.length > 0 ? (
          <div className={styles.lineOffScaleRow}>
            <span aria-hidden="true" />
            <span aria-hidden="true" />
            <div className={styles.lineOffScaleList}>
              {offScaleEntries.map((entry) => (
                <div key={entry.name} className={styles.lineOffScaleEntry}>
                  <span
                    aria-hidden="true"
                    className={styles.lineOffScaleEntryMarker}
                    style={{ color: entry.colour }}
                  />
                  {`${entry.name} ${formatNumber(entry.value)}${valueSuffix ?? ""}`}{" "}
                  <span className={styles.offScaleTag}>
                    {entry.above ? "off scale, above the axis" : "off scale, below the axis"}
                  </span>
                </div>
              ))}
            </div>
          </div>
        ) : null}
        <div className={styles.lineBody}>
          <div className={styles.lineAxis}>
            {yTicks.map((tick) => (
              <span
                key={`y-tick-${tick}`}
                className={styles.lineAxisTick}
                style={{ top: `${clampPercent(yScale.scale(tick))}%` }}
              >
                {formatNumber(tick, tick % 1 === 0 ? 0 : 1)}
              </span>
            ))}
          </div>
          <div className={styles.linePlotColumn}>
            <svg
              aria-hidden="true"
              focusable="false"
              viewBox={`0 0 ${PLOT_UNITS} ${PLOT_UNITS}`}
              preserveAspectRatio="none"
              className={styles.plotOverlay}
            >
              {yTicks.map((tick) => (
                <line
                  key={`grid-${tick}`}
                  x1={0}
                  x2={PLOT_UNITS}
                  y1={clampPercent(yScale.scale(tick))}
                  y2={clampPercent(yScale.scale(tick))}
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
              {paths.map(({ item, seriesIndex }) => {
                let drawing = false;
                let path = "";
                const limit = Math.min(item.values.length, xLabels.length);
                for (let index = 0; index < limit; index += 1) {
                  const value = item.values[index];
                  if (!isValue(value)) {
                    drawing = false;
                    continue;
                  }
                  const x = round(xScale.center(index));
                  const y = round(clampPercent(yScale.scale(value)));
                  path += `${drawing ? " L" : " M"} ${x} ${y}`;
                  drawing = true;
                }
                const colour = colours[seriesIndex];
                return (
                  <path
                    key={item.name}
                    d={path.trim()}
                    className={`${styles.linePath}${
                      item.emphasis === true ? ` ${styles.linePathEmphasis}` : ""
                    }`}
                    style={{ stroke: colour }}
                    vectorEffect="non-scaling-stroke"
                  />
                );
              })}
            </svg>
            {offScaleEntries.map((entry) => (
              <span
                key={`off-scale-${entry.name}`}
                aria-hidden="true"
                className={`${styles.lineOffScaleMarker}${
                  entry.above ? "" : ` ${styles.lineOffScaleMarkerLow}`
                }`}
                style={{ color: entry.colour, left: `${entry.left}%` }}
              />
            ))}
          </div>
          <div className={styles.lineGutter}>
            {endLabels.map((label) => (
              <span
                key={`${label.name}-end`}
                className={styles.endLabel}
                style={{ top: `${label.top}%` }}
              >
                <span
                  aria-hidden="true"
                  className={styles.lineEndSwatch}
                  style={{ color: label.colour }}
                />
                {`${label.name} ${formatNumber(label.value)}${valueSuffix ?? ""}`}
              </span>
            ))}
          </div>
        </div>
        <div className={styles.tickRow}>
          <span aria-hidden="true" />
          <div className={styles.tickTrack}>
            {xLabels.map((label, index) =>
              index % xTickStep === 0 ? (
                <span
                  key={`x-tick-${label}-${index}`}
                  className={styles.tickLabel}
                  style={{ left: `${xScale.center(index)}%` }}
                >
                  {label}
                </span>
              ) : null,
            )}
          </div>
          <span aria-hidden="true" />
        </div>
      </div>
    </ChartFigure>
  );
}
