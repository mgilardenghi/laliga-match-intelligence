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

// The plot is drawn at its natural size: the element is given the pixel width
// and height of its own `viewBox`, so the rendered scale is exactly 1 and every
// label keeps the pixel size the stylesheet declares for it.
const WIDTH = 480;
const LEFT = 14;
const PLOT_LEFT_OFFSET = 16;
const SIDE = 104;
const PANEL_GAP = 16;
const OUTCOMES: readonly ("H" | "D" | "A")[] = ["H", "D", "A"];

// The legend reserves room per entry: the mark and its gap, then about 0.6 em
// per character of the 12 px name, plus a margin between two entries. The
// estimate is generous on purpose, so two names never touch, and a list that
// does not fit on one row wraps onto a second one.
const LEGEND_TOP = 8;
const LEGEND_ROW_HEIGHT = 15;
const LEGEND_MARK = 14;
const LEGEND_CHAR = 7.2;
const LEGEND_GAP = 8;
const LEGEND_RIGHT_MARGIN = 8;
const PANEL_TOP_GAP = 10;
const HEADING_BASELINE = 8;
const PLOT_TOP_GAP = 16;
const AXIS_LABEL_GAP = 14;
const BOTTOM_MARGIN = 6;

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
    const width = LEGEND_MARK + model.length * LEGEND_CHAR;
    if (cursorX > LEFT && cursorX + width > WIDTH - LEGEND_RIGHT_MARGIN) {
      legendRow += 1;
      cursorX = LEFT;
    }
    legend.push({
      model,
      colour: colourByModel.get(model) ?? MUTED_COLOUR,
      shape: shapeByModel.get(model) ?? 0,
      x: cursorX,
      y: LEGEND_TOP + legendRow * LEGEND_ROW_HEIGHT,
    });
    cursorX += width + LEGEND_GAP;
  }

  const legendRows = Math.max(1, legendRow + 1);
  const panelTop = LEGEND_TOP + legendRows * LEGEND_ROW_HEIGHT + PANEL_TOP_GAP;
  const headingY = panelTop + HEADING_BASELINE;
  const plotTop = panelTop + PLOT_TOP_GAP;
  const axisLabelY = plotTop + SIDE + AXIS_LABEL_GAP;
  const height = axisLabelY + BOTTOM_MARGIN;

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
      ids={ids}
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
      {/* The plot is fixed-size and never scales: the element is given the
          pixel width and height of its own `viewBox`, so the rendered scale is
          exactly 1. That is why its labels can stay in the SVG: none of them
          is shrunk by a viewBox, and the scroller takes the width instead.
          The figure is named from outside the drawing: the figcaption carries
          the title id and the hidden span below it carries the description.
          The drawing stays the plot root because a test reads the `H`, `D` and
          `A` headings as `svg text`; its own `<title>` and `<desc>` keep their
          text without ids, so no id in the figure is duplicated. */}
      <div className={styles.calibrationScroller}>
        <svg
          role="img"
          aria-labelledby={ids.labelledBy}
          viewBox={`0 0 ${WIDTH} ${height}`}
          className={styles.svg}
          style={{ width: `${WIDTH}px`, height: `${height}px` }}
          preserveAspectRatio="xMidYMid meet"
        >
          <title>{title}</title>
          <desc>{description}</desc>
          {legend.map((item) => (
            <g key={`legend-${item.model}`}>
              <Mark shape={item.shape} cx={item.x + 4} cy={item.y} radius={4} colour={item.colour} />
              <text
                x={item.x + 11}
                y={item.y}
                dominantBaseline="central"
                className={styles.calibrationLegendText}
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
                  y={headingY}
                  textAnchor="middle"
                  className={styles.calibrationPanelHeading}
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
                  className={styles.calibrationAxisText}
                >
                  0
                </text>
                <text
                  x={plotX - 4}
                  y={plotTop}
                  textAnchor="end"
                  dominantBaseline="central"
                  className={styles.calibrationAxisText}
                >
                  1
                </text>
                <text
                  x={plotX}
                  y={axisLabelY}
                  textAnchor="middle"
                  className={styles.calibrationAxisText}
                >
                  0
                </text>
                <text
                  x={plotX + SIDE}
                  y={axisLabelY}
                  textAnchor="middle"
                  className={styles.calibrationAxisText}
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
      </div>
    </ChartFigure>
  );
}
