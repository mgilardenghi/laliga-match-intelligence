import type { ReactElement, ReactNode } from "react";
import { ChartFigure } from "./ChartFigure";
import { chartIds } from "./chart-ids";
import { formatNumber } from "./format";
import styles from "./charts.module.css";

export type CalibrationPoint = {
  readonly model: string;
  readonly outcome: "H" | "D" | "A";
  readonly meanPredicted: number | null;
  readonly observedFrequency: number | null;
  readonly n: number;
};

export type CalibrationChartProps = {
  readonly points: readonly CalibrationPoint[];
  readonly models?: readonly string[];
  readonly id?: string;
  readonly title?: string;
  readonly description?: string;
  readonly note?: ReactNode;
};

const DEFAULT_TITLE = "Calibration by outcome";
const DEFAULT_DESCRIPTION =
  "Calibration by outcome: mean predicted probability against observed frequency for home wins, draws and away wins. The diagonal marks perfect calibration; dot area grows with the number of matches, with a minimum size so a small bin stays visible.";

const WIDTH = 480;
const LEFT = 14;
const PLOT_LEFT_OFFSET = 16;
const SIDE = 104;
const PANEL_GAP = 16;
const OUTCOMES: readonly ("H" | "D" | "A")[] = ["H", "D", "A"];

// The mark size is a legibility floor: below it a bin is invisible. Above it the
// area is proportional to the number of matches.
const MIN_RADIUS = 1.8;
const MAX_RADIUS = 7;

// Every fallback keeps at least 3:1 against both the light and the dark page
// background, so a chart is legible before the theme tokens exist.
const MODEL_COLOURS: readonly string[] = [
  "var(--cat-1, #b45309)",
  "var(--cat-2, #0f766e)",
  "var(--cat-3, #cc79a7)",
  "var(--cat-4, #33608c)",
];
const MUTED_COLOUR = "var(--muted, #6b7280)";

type LegendItem = {
  readonly model: string;
  readonly colour: string;
  readonly shape: number;
  readonly x: number;
  readonly y: number;
};

/** A model keeps one shape as well as one colour, so identity survives without colour. */
function Mark(props: {
  readonly shape: number;
  readonly cx: number;
  readonly cy: number;
  readonly radius: number;
  readonly colour: string;
}): ReactElement {
  const { shape, cx, cy, radius, colour } = props;

  if (shape === 1) {
    return (
      <rect
        x={cx - radius}
        y={cy - radius}
        width={radius * 2}
        height={radius * 2}
        style={{ fill: colour }}
      />
    );
  }
  if (shape === 2) {
    return (
      <polygon
        points={`${cx},${cy - radius} ${cx + radius},${cy + radius} ${cx - radius},${cy + radius}`}
        style={{ fill: colour }}
      />
    );
  }
  if (shape === 3) {
    return (
      <polygon
        points={`${cx},${cy - radius} ${cx + radius},${cy} ${cx},${cy + radius} ${cx - radius},${cy}`}
        style={{ fill: colour }}
      />
    );
  }
  return <circle cx={cx} cy={cy} r={radius} style={{ fill: colour }} />;
}

export function CalibrationChart(props: CalibrationChartProps): ReactElement {
  const {
    points,
    models,
    id,
    title = DEFAULT_TITLE,
    description = DEFAULT_DESCRIPTION,
    note,
  } = props;
  const ids = chartIds(id ?? title);

  const visiblePoints =
    models === undefined ? points : points.filter((point) => models.includes(point.model));

  const modelOrder: string[] = [];
  for (const point of visiblePoints) {
    if (!modelOrder.includes(point.model)) {
      modelOrder.push(point.model);
    }
  }
  const shapeByModel = new Map<string, number>(
    modelOrder.map((model, index) => [model, index] as const),
  );
  const colourByModel = new Map<string, string>(
    modelOrder.map((model, index) => [model, MODEL_COLOURS[index] ?? MUTED_COLOUR] as const),
  );

  const legend: LegendItem[] = [];
  let cursorX = LEFT;
  let legendRow = 0;
  for (const model of modelOrder) {
    const width = 14 + model.length * 5.2;
    if (cursorX > LEFT && cursorX + width > WIDTH - 8) {
      legendRow += 1;
      cursorX = LEFT;
    }
    legend.push({
      model,
      colour: colourByModel.get(model) ?? MUTED_COLOUR,
      shape: shapeByModel.get(model) ?? 0,
      x: cursorX,
      y: 10 + legendRow * 12,
    });
    cursorX += width + 8;
  }

  const legendRows = Math.max(1, legendRow + 1);
  const panelTop = 10 + legendRows * 12 + 10;
  const plotTop = panelTop + 16;
  const height = plotTop + SIDE + 20;

  const plotted = visiblePoints.filter(
    (point) =>
      point.meanPredicted !== null &&
      point.observedFrequency !== null &&
      Number.isFinite(point.meanPredicted) &&
      Number.isFinite(point.observedFrequency) &&
      point.n > 0,
  );
  const maxN = plotted.reduce((max, point) => Math.max(max, point.n), 1);
  const radiusOf = (n: number): number =>
    Math.sqrt(Math.max(MIN_RADIUS * MIN_RADIUS, (MAX_RADIUS * MAX_RADIUS * n) / maxN));

  return (
    <ChartFigure
      title={title}
      description={description}
      table={{
        caption: "Calibration values",
        columns: ["Model", "Outcome", "Mean predicted", "Observed frequency", "Matches"],
        rows: visiblePoints.map((point) => [
          point.model,
          point.outcome,
          point.meanPredicted === null ? "—" : formatNumber(point.meanPredicted, 3),
          point.observedFrequency === null ? "—" : formatNumber(point.observedFrequency, 3),
          point.n,
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
        {legend.map((item) => (
          <g key={`legend-${item.model}`}>
            <Mark shape={item.shape} cx={item.x + 4} cy={item.y} radius={4} colour={item.colour} />
            <text
              x={item.x + 11}
              y={item.y}
              dominantBaseline="central"
              className={styles.legendText}
            >
              {item.model}
            </text>
          </g>
        ))}
        {OUTCOMES.map((outcome, outcomeIndex) => {
          const panelX = LEFT + outcomeIndex * (PLOT_LEFT_OFFSET + SIDE + PANEL_GAP);
          const plotX = panelX + PLOT_LEFT_OFFSET;
          return (
            <g key={`panel-${outcome}`}>
              <text
                x={plotX + SIDE / 2}
                y={panelTop + 8}
                textAnchor="middle"
                className={styles.panelHeading}
              >
                {outcome}
              </text>
              <line
                x1={plotX}
                y1={plotTop + SIDE}
                x2={plotX + SIDE}
                y2={plotTop}
                style={{ stroke: "var(--muted, #6b7280)", strokeWidth: 1 }}
              />
              <line
                x1={plotX}
                y1={plotTop}
                x2={plotX}
                y2={plotTop + SIDE}
                className={styles.axisLine}
              />
              <line
                x1={plotX}
                y1={plotTop + SIDE}
                x2={plotX + SIDE}
                y2={plotTop + SIDE}
                className={styles.axisLine}
              />
              <text
                x={plotX - 4}
                y={plotTop + SIDE}
                textAnchor="end"
                dominantBaseline="central"
                className={styles.axisText}
              >
                0
              </text>
              <text
                x={plotX - 4}
                y={plotTop}
                textAnchor="end"
                dominantBaseline="central"
                className={styles.axisText}
              >
                1
              </text>
              <text
                x={plotX}
                y={plotTop + SIDE + 11}
                textAnchor="middle"
                className={styles.axisText}
              >
                0
              </text>
              <text
                x={plotX + SIDE}
                y={plotTop + SIDE + 11}
                textAnchor="middle"
                className={styles.axisText}
              >
                1
              </text>
              {plotted
                .filter((point) => point.outcome === outcome)
                .map((point, pointIndex) => {
                  const meanPredicted = point.meanPredicted;
                  const observedFrequency = point.observedFrequency;
                  if (meanPredicted === null || observedFrequency === null) {
                    return null;
                  }
                  return (
                    <Mark
                      key={`${outcome}-${point.model}-${pointIndex}`}
                      shape={shapeByModel.get(point.model) ?? 0}
                      cx={plotX + meanPredicted * SIDE}
                      cy={plotTop + (1 - observedFrequency) * SIDE}
                      radius={radiusOf(point.n)}
                      colour={colourByModel.get(point.model) ?? MUTED_COLOUR}
                    />
                  );
                })}
            </g>
          );
        })}
      </svg>
    </ChartFigure>
  );
}
