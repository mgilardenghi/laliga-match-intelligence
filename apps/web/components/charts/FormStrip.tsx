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
// disagree about the colour behind the letters. The three result tokens are
// identical in both themes, so the derived ink is correct in either one.
const OUTCOME_COLOURS: Record<FormOutcome, OutcomePaint> = {
  W: { fill: "var(--result-w, #0f7b3f)", fallback: "#0f7b3f" },
  D: { fill: "var(--result-d, #5b6472)", fallback: "#5b6472" },
  L: { fill: "var(--result-l, #b3261e)", fallback: "#b3261e" },
};

// Both strips keep an SVG because a table-row test pins its `<title>` and the
// letter-on-square pairs. The drawing therefore never scales: the component
// gives the element the CSS width and height of its `viewBox` in px, so every
// label is drawn at its declared pixel size and a table row keeps its height.
const SQUARE = 32;
const GAP = 8;
const START_X = 8;
const START_Y = 4;
const LABEL_ROW = 20;
const HEIGHT = START_Y + SQUARE + LABEL_ROW;

const COMPACT_SQUARE = 20;
const COMPACT_GAP = 3;
const COMPACT_HEIGHT = 20;

function stripWidth(count: number, square: number, gap: number, padding = 0): number {
  // A strip without a result keeps a one-pixel box instead of a zero-sized
  // element, so the drawing stays valid and its `<title>` stays in the page.
  if (count <= 0) {
    return 1;
  }
  return padding * 2 + count * square + (count - 1) * gap;
}

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
    const width = stripWidth(shown.length, COMPACT_SQUARE, COMPACT_GAP);
    return (
      <svg
        role="img"
        aria-label={title}
        viewBox={`0 0 ${width} ${COMPACT_HEIGHT}`}
        className={styles.formStripSvg}
        style={{ width: `${width}px`, height: `${COMPACT_HEIGHT}px` }}
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
                className={`${styles.formLetter} ${styles.formLetterCompact}`}
                style={{ fill: inkForFill(OUTCOME_COLOURS[result].fallback) }}
              >
                {result}
              </text>
            </g>
          );
        })}
      </svg>
    );
  }

  const width = stripWidth(shown.length, SQUARE, GAP, START_X);
  return (
    <ChartFigure
      ids={ids}
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
      {/* The figure is named from outside the drawing: the figcaption carries
          the title id and the hidden span below it carries the description.
          The drawing stays the plot root because the gallery reads its outcome
          letters through `svg[role="img"]`; its own `<title>` and `<desc>` keep
          their text without ids, so no id in the figure is duplicated. */}
      <svg
        role="img"
        aria-labelledby={ids.labelledBy}
        viewBox={`0 0 ${width} ${HEIGHT}`}
        className={styles.formStripSvg}
        style={{ width: `${width}px`, height: `${HEIGHT}px` }}
      >
        <title>{title}</title>
        <desc>{description}</desc>
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
                className={`${styles.formLetter} ${styles.formLetterFull}`}
                style={{ fill: inkForFill(OUTCOME_COLOURS[result].fallback) }}
              >
                {result}
              </text>
            </g>
          );
        })}
        {shown.length > 0 ? (
          <text
            x={START_X + SQUARE / 2}
            y={START_Y + SQUARE + 14}
            textAnchor="middle"
            className={styles.formNewest}
          >
            newest
          </text>
        ) : null}
      </svg>
    </ChartFigure>
  );
}
