import type { ReactElement, ReactNode } from "react";
import { ChartFigure } from "./ChartFigure";
import { chartIds } from "./chart-ids";
import styles from "./charts.module.css";

export type FormOutcome = "W" | "D" | "L";

export type FormStripProps = {
  readonly results: readonly FormOutcome[];
  readonly id?: string;
  readonly title?: string;
  readonly description?: string;
  readonly note?: ReactNode;
};

const MAX_RESULTS = 5;
const DEFAULT_DESCRIPTION =
  "Five most recent results, newest first, lettered W for a win, D for a draw and L for a loss.";

const OUTCOME_COLOURS: Record<FormOutcome, string> = {
  W: "var(--outcome-w, #15803d)",
  D: "var(--outcome-d, #5f6368)",
  L: "var(--outcome-l, #b91c1c)",
};

const WIDTH = 320;
const HEIGHT = 74;
const SQUARE = 46;
const GAP = 10;
const START_X = 16;
const START_Y = 8;

export function FormStrip(props: FormStripProps): ReactElement {
  const { results, id, description = DEFAULT_DESCRIPTION, note } = props;
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
                style={{ fill: OUTCOME_COLOURS[result] }}
              />
              <text
                x={x + SQUARE / 2}
                y={START_Y + SQUARE / 2}
                textAnchor="middle"
                dominantBaseline="central"
                style={{ fill: "#ffffff", fontSize: 20, fontWeight: 700 }}
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
