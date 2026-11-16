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
};

export type DotPlotProps = {
  readonly metrics: readonly string[];
  readonly rows: readonly {
    readonly model: string;
    readonly values: readonly (number | null)[];
  }[];
  readonly references?: readonly DotPlotReference[];
  readonly id?: string;
  readonly title?: string;
  readonly description?: string;
  readonly note?: ReactNode;
};

const DEFAULT_TITLE = "Model metrics compared";
const DEFAULT_DESCRIPTION =
  "One panel per metric, with one row per model. Filled dots show each model's value; dashed lines mark reference values.";

const WIDTH = 480;
const PLOT_LEFT = 100;
const PLOT_RIGHT = 466;
const ROW_HEIGHT = 20;
const HEADER_HEIGHT = 38;
const AXIS_LABEL_HEIGHT = 16;
const PANEL_GAP = 14;
// Reference labels sit on two rows so two references with nearly the same value
// cannot overwrite each other.
const LABEL_ROWS = 2;
const LABEL_ROW_GAP = 12;
const LABEL_HALF_WIDTH = 24;

// Every fallback keeps at least 3:1 against both the light and the dark page
// background, so a chart is legible before the theme tokens exist.
const MODEL_COLOURS: readonly string[] = [
  "var(--cat-1, #b45309)",
  "var(--cat-2, #0f766e)",
  "var(--cat-3, #cc79a7)",
  "var(--cat-4, #33608c)",
];
const MUTED_COLOUR = "var(--muted, #6b7280)";

type PlacedReference = DotPlotReference & {
  readonly x: number;
  readonly row: number;
};

/**
 * Place reference labels on the lowest row that still has room, so labels of
 * two references close together stay readable.
 */
function placeReferences(
  references: readonly DotPlotReference[],
  scale: (value: number) => number,
): readonly PlacedReference[] {
  const ordered = [...references].sort((left, right) => left.value - right.value);
  const occupied: { low: number; high: number }[][] = Array.from(
    { length: LABEL_ROWS },
    () => [],
  );
  const placed: PlacedReference[] = [];
  for (const reference of ordered) {
    const x = scale(reference.value);
    const low = x - LABEL_HALF_WIDTH;
    const high = x + LABEL_HALF_WIDTH;
    const row = occupied.findIndex((spans) =>
      spans.every((span) => high <= span.low || low >= span.high),
    );
    const rowIndex = row >= 0 ? row : LABEL_ROWS - 1;
    occupied[rowIndex].push({ low, high });
    placed.push({ ...reference, x, row: rowIndex });
  }
  return placed;
}

export function DotPlot(props: DotPlotProps): ReactElement {
  const {
    metrics,
    rows,
    references,
    id,
    title = DEFAULT_TITLE,
    description = DEFAULT_DESCRIPTION,
    note,
  } = props;
  const ids = chartIds(id ?? title);

  const panelHeight =
    HEADER_HEIGHT + Math.max(1, rows.length) * ROW_HEIGHT + 4 + AXIS_LABEL_HEIGHT;
  const height =
    10 + Math.max(1, metrics.length) * panelHeight + Math.max(0, metrics.length - 1) * PANEL_GAP;

  return (
    <ChartFigure
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
              value === null || value === undefined || !Number.isFinite(value)
                ? "—"
                : formatNumber(value, 3),
            ];
          }),
        ),
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
        {metrics.map((metric, metricIndex) => {
          const panelTop = 10 + metricIndex * (panelHeight + PANEL_GAP);
          const rowsTop = panelTop + HEADER_HEIGHT;
          const axisY = rowsTop + Math.max(1, rows.length) * ROW_HEIGHT + 4;
          const metricReferences = (references ?? []).filter(
            (reference) => reference.metric === metric,
          );
          const values = rows
            .map((row) => row.values[metricIndex])
            .filter(
              (value): value is number =>
                value !== null && value !== undefined && Number.isFinite(value),
            )
            .concat(metricReferences.map((reference) => reference.value));
          // A dot plot has no zero baseline to preserve, so the axis spans the
          // data instead of starting at zero: otherwise every model collapses
          // onto one x position and the chart says nothing.
          const minimum = values.length > 0 ? Math.min(...values) : 0;
          const maximum = values.length > 0 ? Math.max(...values) : 1;
          const span = maximum - minimum;
          const padding = span > 0 ? span * 0.08 : Math.max(0.05, Math.abs(maximum) * 0.1);
          const scale = linearScale(
            [minimum - padding, maximum + padding],
            [PLOT_LEFT, PLOT_RIGHT],
          );
          const ticks = scale.ticks(4);
          const placed = placeReferences(metricReferences, (value) => scale.scale(value));

          return (
            <g key={metric}>
              <text x={PLOT_LEFT} y={panelTop + 12} className={styles.panelHeading}>
                {metric}
              </text>
              {ticks.map((tick) => {
                const x = scale.scale(tick);
                return (
                  <g key={`${metric}-tick-${tick}`}>
                    <line x1={x} y1={rowsTop - 4} x2={x} y2={axisY} className={styles.gridLine} />
                    <text x={x} y={axisY + 12} textAnchor="middle" className={styles.axisText}>
                      {formatNumber(tick, Math.abs(tick) < 10 ? 2 : 0)}
                    </text>
                  </g>
                );
              })}
              <line x1={PLOT_LEFT} y1={axisY} x2={PLOT_RIGHT} y2={axisY} className={styles.axisLine} />
              {rows.map((row, rowIndex) => {
                const value = row.values[metricIndex];
                const y = rowsTop + rowIndex * ROW_HEIGHT + ROW_HEIGHT / 2;
                const colour = MODEL_COLOURS[rowIndex] ?? MUTED_COLOUR;
                return (
                  <g key={`${metric}-${row.model}`}>
                    <text
                      x={PLOT_LEFT - 8}
                      y={y}
                      textAnchor="end"
                      dominantBaseline="central"
                      className={styles.segmentName}
                    >
                      {row.model}
                    </text>
                    {value !== null && value !== undefined && Number.isFinite(value) ? (
                      <circle cx={scale.scale(value)} cy={y} r={4} style={{ fill: colour }} />
                    ) : null}
                  </g>
                );
              })}
              {placed.map((reference) => (
                <g key={`${metric}-${reference.label}`}>
                  <line
                    x1={reference.x}
                    y1={rowsTop - 4}
                    x2={reference.x}
                    y2={axisY}
                    style={{
                      stroke: "var(--muted, #6b7280)",
                      strokeWidth: 1,
                      strokeDasharray: "3 3",
                    }}
                  />
                  <text
                    x={reference.x}
                    y={rowsTop - 8 - reference.row * LABEL_ROW_GAP}
                    textAnchor="middle"
                    className={styles.axisText}
                  >
                    {reference.label}
                  </text>
                </g>
              ))}
            </g>
          );
        })}
      </svg>
    </ChartFigure>
  );
}
