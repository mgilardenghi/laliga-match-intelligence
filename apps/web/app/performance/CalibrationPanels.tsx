import type { ReactElement } from "react";

import { ChartFigure, chartIds } from "@/components/charts";
import { EM_DASH, formatCount, formatValue } from "@/lib/format";
import { outcomeLabel } from "@/lib/labels";
import type { MetricsSubset } from "@/lib/params";

import { calibrationHref } from "./metrics";
import styles from "./calibration.module.css";

export type CalibrationPoint = {
  readonly outcome: "H" | "D" | "A";
  readonly binIndex: number;
  readonly meanPredicted: number | null;
  readonly observedFrequency: number | null;
  readonly n: number;
};

export type CalibrationModel = {
  /** The raw model name the links are built from. */
  readonly name: string;
  /** The display label the panel heading and the picker print. */
  readonly label: string;
  readonly points: readonly CalibrationPoint[];
};

export type CalibrationPanelsProps = {
  /** The models that hold at least one bin, in the run's order. */
  readonly models: readonly CalibrationModel[];
  /** The raw name of the drawn model, always one of `models`. */
  readonly selected: string;
  readonly subset: MetricsSubset;
};

// The drawing is fixed-size and given the pixel width and height of its own
// `viewBox`, so the rendered scale is exactly 1 and its labels keep the pixel
// sizes this module declares for them.
const WIDTH = 320;
const HEIGHT = 130;
const PANEL_SIDE = 90;
const PANEL_TOP = 20;
const PANEL_BOTTOM = PANEL_TOP + PANEL_SIDE;
const PANEL_LEFT: readonly number[] = [12, 115, 218];
const OUTCOMES: readonly ("H" | "D" | "A")[] = ["H", "D", "A"];
const HEADING_BASELINE = 16;
const TICK_BASELINE = 122;
const MIN_RADIUS = 1.5;
const MAX_RADIUS_GROWTH = 2.5;

const DESCRIPTION =
  "Mean predicted probability against observed frequency for home wins, draws and away wins, one point per calibration bin. The diagonal marks perfect calibration and the dot area grows with the number of matches.";

const NOTE =
  "Calibration bins carry neither a subset nor a season, so this chart is the same whichever subset is chosen above.";

/** Keep only a probability the API actually answered. */
function finiteOrNull(value: number | null): number | null {
  return value !== null && Number.isFinite(value) ? value : null;
}

/**
 * The calibration bins of one model, one square panel per outcome.
 *
 * The panels are drawn at their natural size: the element is given the pixel
 * width and height of its `viewBox`, so a declared font size is the size the
 * reader sees. The figure names the drawing from outside it -- the caption
 * carries the title id and the hidden description the other one -- which is why
 * the SVG holds no `<title>` or `<desc>` of its own and no id is duplicated.
 *
 * The picker is a list of links, not a control: the page is a Server
 * Component, so the selected model is part of the URL and every choice is a
 * navigation the server renders.
 */
export function CalibrationPanels(
  props: CalibrationPanelsProps,
): ReactElement {
  const { models, selected, subset } = props;
  const drawn = models.find((model) => model.name === selected) ?? models[0];
  const label = drawn?.label ?? selected;
  const points = drawn?.points ?? [];
  const title = `Calibration by outcome — ${label}`;
  const ids = chartIds(title, "backtest-calibration");

  // The largest bin sizes the mark, so a bin of one match stays a visible dot
  // and a bin of the whole sample reaches the largest radius. A model whose
  // bins are all empty keeps the minimum size instead of dividing by zero.
  const maxN = Math.max(1, ...points.map((point) => point.n));

  const columns = ["Outcome", "Bin", "Mean predicted", "Observed", "Matches"];

  return (
    <ChartFigure
      ids={ids}
      title={title}
      description={DESCRIPTION}
      table={{
        caption: title,
        columns,
        rows: points.map((point) => [
          outcomeLabel(point.outcome),
          formatCount(point.binIndex),
          point.meanPredicted === null
            ? EM_DASH
            : formatValue(point.meanPredicted, 3),
          point.observedFrequency === null
            ? EM_DASH
            : formatValue(point.observedFrequency, 3),
          formatCount(point.n),
        ]),
      }}
      note={NOTE}
    >
      <div className={styles.scroller}>
        <svg
          role="img"
          aria-labelledby={ids.labelledBy}
          viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
          className={styles.svg}
          style={{ width: `${WIDTH}px`, height: `${HEIGHT}px` }}
        >
          {OUTCOMES.map((outcome, index) => {
            const left = PANEL_LEFT[index];
            return (
              <g key={outcome}>
                <text
                  className={styles.heading}
                  x={left + PANEL_SIDE / 2}
                  y={HEADING_BASELINE}
                  textAnchor="middle"
                >
                  {outcome}
                </text>
                <rect
                  className={styles.panel}
                  x={left}
                  y={PANEL_TOP}
                  width={PANEL_SIDE}
                  height={PANEL_SIDE}
                />
                <line
                  className={styles.diagonal}
                  x1={left}
                  y1={PANEL_BOTTOM}
                  x2={left + PANEL_SIDE}
                  y2={PANEL_TOP}
                />
                <text
                  className={styles.tick}
                  x={left}
                  y={TICK_BASELINE}
                  textAnchor="start"
                >
                  0
                </text>
                <text
                  className={styles.tick}
                  x={left + PANEL_SIDE}
                  y={TICK_BASELINE}
                  textAnchor="end"
                >
                  1
                </text>
                {points
                  .filter(
                    (point) =>
                      point.outcome === outcome &&
                      point.n > 0 &&
                      finiteOrNull(point.meanPredicted) !== null &&
                      finiteOrNull(point.observedFrequency) !== null,
                  )
                  .map((point) => {
                    const meanPredicted = finiteOrNull(point.meanPredicted) ?? 0;
                    const observed = finiteOrNull(point.observedFrequency) ?? 0;
                    return (
                      <circle
                        key={`${outcome}-${point.binIndex}`}
                        className={styles.dot}
                        cx={left + meanPredicted * PANEL_SIDE}
                        cy={PANEL_BOTTOM - observed * PANEL_SIDE}
                        r={MIN_RADIUS + MAX_RADIUS_GROWTH * (point.n / maxN)}
                      />
                    );
                  })}
              </g>
            );
          })}
        </svg>
      </div>

      <ul className={styles.picker}>
        {models.map((model) => (
          <li key={model.name}>
            <a
              className={styles.pickerLink}
              href={calibrationHref(subset, model.name)}
              aria-current={model.name === selected ? "true" : undefined}
            >
              {model.label}
            </a>
          </li>
        ))}
      </ul>
    </ChartFigure>
  );
}
