import type { ReactElement, ReactNode } from "react";
import { ChartFigure } from "./ChartFigure";
import { chartIds } from "./chart-ids";
import { inkForFill } from "./contrast";
import { formatPercent } from "./format";
import styles from "./charts.module.css";

export type PositionHeatmapProps = {
  readonly teams: readonly string[];
  readonly positions: readonly (readonly number[])[];
  readonly qualification: {
    readonly uclPositions: number;
    readonly europePositions: number;
    readonly relegationPositions: number;
  };
  readonly id?: string;
  readonly title?: string;
  readonly description?: string;
  readonly note?: ReactNode;
};

const DEFAULT_TITLE = "Projected finishing position probabilities";
const DEFAULT_DESCRIPTION =
  "Heatmap of each team's probability of finishing in every league position. Rows follow the projected table; columns are positions 1 to n.";

// A cell is wide and tall enough that the widest label, "100%" at 11 px, fits
// inside it with a margin on both sides, so no cell label touches its neighbour
// or overflows its own rect. The 36 px cell is sized against the widest sans
// fallback metric (about 2.83 em for "100%"), not just the narrowest one; the
// horizontal scroller absorbs the grid width that follows.
const CELL_WIDTH = 36;
const ROW_HEIGHT = 26;
const HEADER_HEIGHT = 20;
const ZONE_LABEL_HEIGHT = 16;

type SequentialPaint = {
  readonly fill: string;
  readonly fallback: string;
};

// The fill is a token with a hex fallback, and the label ink is derived from
// that same fallback: fill and ink come from one declaration, so they cannot
// disagree about the colour behind the percentage. The gallery e2e test guards
// a token and its fallback staying identical.
const SEQUENTIAL_COLOURS: readonly SequentialPaint[] = [
  { fill: "var(--seq-0, #f0f4f9)", fallback: "#f0f4f9" },
  { fill: "var(--seq-1, #dbe9f6)", fallback: "#dbe9f6" },
  { fill: "var(--seq-2, #9ecae1)", fallback: "#9ecae1" },
  { fill: "var(--seq-3, #6baed6)", fallback: "#6baed6" },
  { fill: "var(--seq-4, #3f8fc4)", fallback: "#3f8fc4" },
  { fill: "var(--seq-5, #2171b5)", fallback: "#2171b5" },
  { fill: "var(--seq-6, #08519c)", fallback: "#08519c" },
  { fill: "var(--seq-7, #083f78)", fallback: "#083f78" },
  { fill: "var(--seq-8, #062d55)", fallback: "#062d55" },
];

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function bucket(probability: number): number {
  const value = Number.isFinite(probability) ? probability : 0;
  if (value <= 0) return 0;
  if (value < 0.01) return 1;
  if (value < 0.03) return 2;
  if (value < 0.05) return 3;
  if (value < 0.1) return 4;
  if (value < 0.2) return 5;
  if (value < 0.35) return 6;
  if (value < 0.55) return 7;
  return 8;
}

export function PositionHeatmap(props: PositionHeatmapProps): ReactElement {
  const { teams, positions, qualification, id, title = DEFAULT_TITLE, description = DEFAULT_DESCRIPTION, note } = props;
  const ids = chartIds(id ?? title);

  const columnCount = (() => {
    if (positions.length !== teams.length) {
      throw new Error(
        `PositionHeatmap needs one probability row per team, but received ${teams.length} teams and ${positions.length} rows.`,
      );
    }
    const width = positions[0]?.length ?? 0;
    if (positions.some((row) => row.length !== width)) {
      throw new Error(
        "PositionHeatmap needs one probability per league position in every row; a ragged row would be drawn as a measured zero.",
      );
    }
    return width;
  })();
  const gridWidth = Math.max(1, columnCount) * CELL_WIDTH;
  const gridHeight = ZONE_LABEL_HEIGHT + HEADER_HEIGHT + teams.length * ROW_HEIGHT;
  const cellsTop = ZONE_LABEL_HEIGHT + HEADER_HEIGHT;

  const uclBoundary = clamp(qualification.uclPositions, 0, columnCount);
  const rawEurope = qualification.europePositions;
  // Accept both conventions: an absolute European cutoff, or European places
  // counted after the UCL places.
  const europeBoundary = clamp(
    rawEurope > uclBoundary ? rawEurope : uclBoundary + rawEurope,
    uclBoundary,
    columnCount,
  );
  const relegationStart = clamp(columnCount - qualification.relegationPositions, 0, columnCount);

  const boundaries = [uclBoundary, europeBoundary, relegationStart].filter(
    (boundary, index, all) =>
      boundary > 0 && boundary < columnCount && all.indexOf(boundary) === index,
  );

  const zones: readonly { readonly label: string; readonly from: number; readonly to: number }[] = [
    { label: "UCL", from: 0, to: uclBoundary },
    { label: "Europe", from: uclBoundary, to: europeBoundary },
    { label: "Relegation", from: relegationStart, to: columnCount },
  ];

  return (
    <ChartFigure
      ids={ids}
      title={title}
      description={description}
      table={{
        caption: "Finishing position probabilities",
        columns: ["Team", ...Array.from({ length: columnCount }, (_, index) => String(index + 1))],
        rows: teams.map((team, rowIndex) => [
          team,
          ...Array.from({ length: columnCount }, (_, columnIndex) => {
            const value = positions[rowIndex][columnIndex];
            return formatPercent(value, 1);
          }),
        ]),
      }}
      note={note}
    >
      <div className={styles.heatmapScroller}>
        <div className={styles.heatmapLayout}>
          <div className={styles.heatmapLabels} aria-hidden="true">
            <div style={{ height: `${cellsTop}px` }} />
            {teams.map((team) => (
              <div key={team} className={styles.heatmapTeam} style={{ height: `${ROW_HEIGHT}px` }}>
                {team}
              </div>
            ))}
          </div>
          {/* This plot is fixed-size and never scales: the element is given the
              pixel width and height of its own `viewBox`, so the rendered scale
              is exactly 1. That is why its cell labels, column numbers and zone
              labels can stay in the SVG: none of them is ever shrunk by a
              viewBox, and the horizontal scroller takes the width instead.
              The figure is named from outside the drawing: the figcaption
              carries the title id and the hidden span below it carries the
              description. The drawing stays the plot root because the gallery
              reads its cell labels through `svg[role="img"]`; its own `<title>`
              and `<desc>` keep their text without ids, so no id in the figure
              is duplicated. */}
          <svg
            role="img"
            aria-labelledby={ids.labelledBy}
            viewBox={`0 0 ${gridWidth} ${gridHeight}`}
            className={`${styles.svg} ${styles.heatmapSvg}`}
            style={{ width: `${gridWidth}px`, height: `${gridHeight}px` }}
            preserveAspectRatio="xMidYMid meet"
          >
            <title>{title}</title>
            <desc>{description}</desc>
            {Array.from({ length: columnCount }, (_, columnIndex) => (
              <text
                key={`header-${columnIndex}`}
                x={(columnIndex + 0.5) * CELL_WIDTH}
                y={ZONE_LABEL_HEIGHT + HEADER_HEIGHT - 7}
                textAnchor="middle"
                className={styles.heatmapHeaderText}
              >
                {columnIndex + 1}
              </text>
            ))}
            {teams.map((team, rowIndex) =>
              Array.from({ length: columnCount }, (_, columnIndex) => {
                const value = positions[rowIndex][columnIndex];
                const step = bucket(value);
                const paint = SEQUENTIAL_COLOURS[step];
                const x = columnIndex * CELL_WIDTH;
                const y = cellsTop + rowIndex * ROW_HEIGHT;
                return (
                  <g key={`${rowIndex}-${columnIndex}`}>
                    <rect
                      x={x + 0.5}
                      y={y + 0.5}
                      width={CELL_WIDTH - 1}
                      height={ROW_HEIGHT - 1}
                      style={{ fill: paint.fill }}
                    />
                    {value >= 0.05 ? (
                      <text
                        x={x + CELL_WIDTH / 2}
                        y={y + ROW_HEIGHT / 2}
                        textAnchor="middle"
                        dominantBaseline="central"
                        className={styles.heatmapCellText}
                        style={{ fill: inkForFill(paint.fallback) }}
                      >
                        {formatPercent(value, 0)}
                      </text>
                    ) : null}
                  </g>
                );
              }),
            )}
            {boundaries.map((boundary) => (
              <line
                key={`boundary-${boundary}`}
                x1={boundary * CELL_WIDTH}
                y1={ZONE_LABEL_HEIGHT}
                x2={boundary * CELL_WIDTH}
                y2={gridHeight}
                style={{ stroke: "var(--border, #d4d4d8)", strokeWidth: 1.4 }}
              />
            ))}
            {zones.map((zone) =>
              zone.to > zone.from ? (
                <text
                  key={`zone-${zone.label}`}
                  x={((zone.from + zone.to) / 2) * CELL_WIDTH}
                  y={12}
                  textAnchor="middle"
                  className={styles.heatmapZoneLabel}
                >
                  {zone.label}
                </text>
              ) : null,
            )}
          </svg>
        </div>
      </div>
    </ChartFigure>
  );
}
