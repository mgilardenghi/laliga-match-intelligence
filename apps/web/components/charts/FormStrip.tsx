import type { ReactElement, ReactNode } from "react";
import { ChartFigure } from "./ChartFigure";
import { chartIds } from "./chart-ids";
import { inkForFill } from "./contrast";
import styles from "./charts.module.css";

export type FormOutcome = "W" | "D" | "L";

export type FormStripProps = {
  readonly results: readonly FormOutcome[];
  readonly id?: string;
  readonly title?: string;
  readonly description?: string;
  readonly note?: ReactNode;
  /**
   * Draw only the outcome squares. The compact strip has no figure, caption,
   * note or data table; it keeps an accessible name through `aria-label` and
   * its `<title>`.
   */
  readonly compact?: boolean;
};

const MAX_RESULTS = 5;
const DEFAULT_DESCRIPTION =
  "Five most recent results, newest first, lettered W for a win, D for a draw and L for a loss.";

type OutcomePaint = {
  readonly fill: string;
  readonly fallback: string;
};

// The fill is a token with a hex fallback, and the label ink is derived from
// that same fallback: fill and ink come from one declaration, so they cannot
// disagree about the colour behind the letters. The gallery e2e test guards a
// token and its fallback staying identical.
const OUTCOME_COLOURS: Record<FormOutcome, OutcomePaint> = {
  W: { fill: "var(--outcome-w, #15803d)", fallback: "#15803d" },
  D: { fill: "var(--outcome-d, #5f6368)", fallback: "#5f6368" },
  L: { fill: "var(--outcome-l, #b91c1c)", fallback: "#b91c1c" },
};

const WIDTH = 320;
const HEIGHT = 74;
const SQUARE = 46;
const GAP = 10;
const START_X = 16;
const START_Y = 8;

const COMPACT_WIDTH = 126;
const COMPACT_HEIGHT = 26;
const COMPACT_SQUARE = 22;
const COMPACT_GAP = 4;

export function FormStrip(props: FormStripProps): ReactElement {
  const {
    results,
    id,
    description = DEFAULT_DESCRIPTION,
    note,
    compact = false,
  } = props;
  const shown = results.slice(0, MAX_RESULTS);
  // A form string is the last five matches. Dropping the extra results would
  // hide data, so a longer list is a caller error instead.
  if (results.length > MAX_RESULTS) {
    throw new Error(
      `FormStrip renders at most ${MAX_RESULTS} results, but received ${results.length}. Pass the ${MAX_RESULTS} most recent ones.`,
    );
  }
  const title = props.title ?? `Form: ${shown.join(", ")}`;
  const ids = chartIds(id ?? title);

  if (compact) {
    return (
      <svg
        role="img"
        aria-label={title}
        viewBox={`0 0 ${COMPACT_WIDTH} ${COMPACT_HEIGHT}`}
        className={`${styles.svg} ${styles.formCompactSvg}`}
        preserveAspectRatio="xMidYMid meet"
      >
        <title id={ids.titleId}>{title}</title>
        {shown.map((result, index) => {
          const x = index * (COMPACT_SQUARE + COMPACT_GAP);
          return (
            <g key={`${index}-${result}`}>
              <rect
                x={x}
                y={0}
                width={COMPACT_SQUARE}
                height={COMPACT_SQUARE}
                rx={3}
                style={{ fill: OUTCOME_COLOURS[result].fill }}
              />
              <text
                x={x + COMPACT_SQUARE / 2}
                y={COMPACT_SQUARE / 2}
                textAnchor="middle"
                dominantBaseline="central"
                style={{
                  fill: inkForFill(OUTCOME_COLOURS[result].fallback),
                  fontSize: 14,
                  fontWeight: 700,
                }}
              >
                {result}
              </text>
            </g>
          );
        })}
      </svg>
    );
  }

  return (
    <ChartFigure
      title={title}
      description={description}
      table={{
        caption: "Recent form, newest first",
        columns: ["Match", "Outcome"],
        rows: shown.map((result, index) => [
          index === 0 ? "1 (newest)" : String(index + 1),
          result,
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
        <desc id={ids.descId}>{description}</desc>
        {shown.map((result, index) => {
          const x = START_X + index * (SQUARE + GAP);
          return (
            <g key={`${index}-${result}`}>
              <rect
                x={x}
                y={START_Y}
                width={SQUARE}
                height={SQUARE}
                rx={4}
                style={{ fill: OUTCOME_COLOURS[result].fill }}
              />
              <text
                x={x + SQUARE / 2}
                y={START_Y + SQUARE / 2}
                textAnchor="middle"
                dominantBaseline="central"
                style={{
                  fill: inkForFill(OUTCOME_COLOURS[result].fallback),
                  fontSize: 20,
                  fontWeight: 700,
                }}
              >
                {result}
              </text>
            </g>
          );
        })}
        {shown.length > 0 ? (
          <text
            x={START_X + SQUARE / 2}
            y={START_Y + SQUARE + 12}
            textAnchor="middle"
            className={styles.axisText}
          >
            newest
          </text>
        ) : null}
      </svg>
    </ChartFigure>
  );
}
