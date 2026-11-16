import type { ReactElement, ReactNode } from "react";
import { ChartFigure } from "./ChartFigure";
import { chartIds } from "./chart-ids";
import { formatPercent } from "./format";
import styles from "./charts.module.css";

export type ProbabilityBarProps = {
  readonly home: { readonly name: string; readonly probability: number };
  readonly draw: { readonly probability: number };
  readonly away: { readonly name: string; readonly probability: number };
  readonly id?: string;
  readonly title?: string;
  readonly description?: string;
  readonly note?: ReactNode;
};

const DEFAULT_TITLE = "Home win, draw and away win probabilities";
const DEFAULT_DESCRIPTION =
  "Stacked bar of the home win, draw and away win probabilities. Every segment is labelled with its outcome letter and percentage.";

const WIDTH = 480;
const HEIGHT = 120;
const PLOT_LEFT = 12;
const PLOT_RIGHT = 468;
const BAR_Y = 26;
const BAR_HEIGHT = 34;
// One row per segment: with three labels and three rows a free row always
// exists, so two labels can never be drawn on top of each other.
const LABEL_ROWS = 3;
const LABEL_ROW_Y = 80;
const LABEL_ROW_GAP = 14;
const CHAR_WIDTH = 5.4;

// Every fallback keeps at least 3:1 against both the light and the dark page
// background, so a chart is legible before the theme tokens exist.
const SEGMENT_COLOURS: readonly string[] = [
  "var(--cat-1, #b45309)",
  "var(--cat-2, #0f766e)",
  "var(--cat-3, #cc79a7)",
];

type Segment = {
  readonly key: "H" | "D" | "A";
  readonly probability: number;
  readonly colour: string;
};

type LabelledSegment = Segment & {
  readonly start: number;
  readonly width: number;
  readonly label: string;
  readonly labelX: number;
  readonly labelRow: number;
  readonly labelWidth: number;
};

export function ProbabilityBar(props: ProbabilityBarProps): ReactElement {
  const { home, draw, away, id, title = DEFAULT_TITLE, description = DEFAULT_DESCRIPTION, note } =
    props;
  const ids = chartIds(id ?? title);
  const plotWidth = PLOT_RIGHT - PLOT_LEFT;

  const segments: readonly Segment[] = [
    { key: "H", probability: home.probability, colour: SEGMENT_COLOURS[0] },
    { key: "D", probability: draw.probability, colour: SEGMENT_COLOURS[1] },
    { key: "A", probability: away.probability, colour: SEGMENT_COLOURS[2] },
  ];
  const total = segments.reduce((sum, segment) => sum + Math.max(0, segment.probability), 0);
  // A bar whose widths disagree with its own labels would be a lie. Each
  // segment is drawn as its share of the total, and its label shows that same
  // share. A total that is not 100 % is stated in the chart; the table keeps
  // the raw inputs.
  const denominator = total > 0 ? total : 1;
  const scaled = Math.abs(total - 1) > 1e-6;

  let cursor = PLOT_LEFT;
  const occupied: { low: number; high: number }[][] = Array.from({ length: LABEL_ROWS }, () => []);
  const labelled: LabelledSegment[] = segments.map((segment) => {
    const width = (Math.max(0, segment.probability) / denominator) * plotWidth;
    const start = cursor;
    cursor += width;
    const label = `${segment.key} ${formatPercent(Math.max(0, segment.probability) / denominator)}`;
    const labelWidth = label.length * CHAR_WIDTH;
    const half = labelWidth / 2;
    const centre = Math.min(PLOT_RIGHT - half, Math.max(PLOT_LEFT + half, start + width / 2));
    const low = centre - half;
    const high = centre + half;
    const freeRow = occupied.findIndex((spans) =>
      spans.every((span) => high <= span.low || low >= span.high),
    );
    const labelRow = freeRow >= 0 ? freeRow : LABEL_ROWS - 1;
    occupied[labelRow].push({ low, high });
    return { ...segment, start, width, label, labelX: centre, labelRow, labelWidth };
  });

  return (
    <ChartFigure
      title={title}
      description={description}
      table={{
        caption: "Outcome probabilities",
        columns: ["Outcome", "Team", "Probability"],
        rows: [
          ["H", home.name, formatPercent(home.probability)],
          ["D", "Draw", formatPercent(draw.probability)],
          ["A", away.name, formatPercent(away.probability)],
        ],
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
        <desc id={ids.descId}>{description}</desc>
        <text x={PLOT_LEFT} y={16} textAnchor="start" className={styles.segmentName}>
          {home.name}
        </text>
        <text x={PLOT_RIGHT} y={16} textAnchor="end" className={styles.segmentName}>
          {away.name}
        </text>
        <rect
          x={PLOT_LEFT}
          y={BAR_Y}
          width={plotWidth}
          height={BAR_HEIGHT}
          rx={3}
          style={{ fill: "var(--border, #d4d4d8)", opacity: 0.35 }}
        />
        {labelled.map((segment) => (
          <rect
            key={`bar-${segment.key}`}
            x={segment.start}
            y={BAR_Y}
            width={segment.width}
            height={BAR_HEIGHT}
            style={{ fill: segment.colour }}
          />
        ))}
        {labelled.map((segment) => {
          const labelY = LABEL_ROW_Y + segment.labelRow * LABEL_ROW_GAP;
          const segmentCentre = segment.start + segment.width / 2;
          return (
            <g key={`label-${segment.key}`}>
              <line
                x1={segment.labelX}
                y1={labelY - 6}
                x2={segmentCentre}
                y2={BAR_Y + BAR_HEIGHT + 2}
                className={styles.leaderLine}
              />
              <text
                x={segment.labelX}
                y={labelY}
                textAnchor="middle"
                className={styles.probLabel}
              >
                {segment.label}
              </text>
            </g>
          );
        })}
        {scaled ? (
          <text x={PLOT_LEFT} y={HEIGHT - 6} textAnchor="start" className={styles.axisText}>
            {`The inputs add up to ${formatPercent(total)}; each segment shows its share of that total.`}
          </text>
        ) : null}
      </svg>
    </ChartFigure>
  );
}
