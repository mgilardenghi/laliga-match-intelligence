/**
 * The mirrored per-match profile of the team page.
 *
 * One row per metric with two bars facing each other: the club's own average
 * extends to the right and the average its opponents produced extends to the
 * left, so a club that shoots a lot and concedes a lot reads as two long bars,
 * and each bar prints its value at its outer end. The league average is a tick
 * on both sides of the same row, because it is one number and the two bars are
 * measured from the same zero.
 *
 * Each row carries its own scale, because the metrics count different things.
 *
 * The component draws only the SVG. The heading, the description, the data
 * table and the coverage note belong to the `ChartFigure` the page wraps around
 * it, so the chart never states a number the table below cannot back up. A
 * metric the season did not record is `null`, and `null` draws no bar: a zero
 * length bar would claim the club averaged zero.
 */
import type { ReactElement } from "react";

import { chartIds, linearScale, niceMax } from "@/components/charts";
import type { GetData } from "@/lib/api";
import { formatValue } from "@/lib/format";

import styles from "./team-page.module.css";

/** One side's per-match averages, as the season-statistics endpoint types them. */
export type StatLine =
  GetData<"/v1/teams/{team_id}/season-stats">["per_match"]["for"];

/** One metric of the chart: the contract field and the words a reader sees. */
export type ProfileMetric = {
  readonly key: keyof StatLine;
  readonly label: string;
};

/**
 * The metrics of the chart, in the order the page renders them.
 *
 * The order goes from the outcomes (goals) to the most effort-like inputs and
 * ends with the discipline counters. Corners is the one field of the contract
 * left out: the page's chart answers "how much did the club create and allow",
 * and a corner count beside shots and expected goals would repeat the question.
 */
export const PROFILE_METRICS: readonly ProfileMetric[] = [
  { key: "goals", label: "Goals" },
  { key: "shots", label: "Shots" },
  { key: "shots_on_target", label: "Shots on target" },
  { key: "xg", label: "Expected goals" },
  { key: "fouls", label: "Fouls" },
  { key: "yellow", label: "Yellow cards" },
  { key: "red", label: "Red cards" },
];

export type ProfileBarsProps = {
  readonly forLine: StatLine;
  readonly againstLine: StatLine;
  readonly leagueLine: StatLine;
  readonly id?: string;
};

const TITLE = "Per-match averages of the club and of its opponents";
const DESCRIPTION =
  "One row per metric, each on its own scale. The bar to the right is the club's own per-match average, the bar to the left the average of the clubs it faced, and the tick marks the league average. A metric the season did not record has no bar.";

// The drawing is rendered at its natural size: the element is given the pixel
// width and height of its own `viewBox`, so the rendered scale is exactly 1 and
// every label keeps the pixel size the stylesheet declares. It is also narrow
// enough for a phone: the metric label sits above its row instead of beside it,
// so the whole mirrored pair fits in 300 px and nothing hides behind a sideways
// scroll. The scroller stays only as a safety net for a narrower window.
const WIDTH = 300;
// The value of a bar prints at its outer end, outside the plot, so both sides
// keep a gutter of their own: it holds a formatted value such as "12.34" at
// 12 px without running past the drawing.
const PLOT_LEFT = 46;
const PLOT_RIGHT = 254;
const VALUE_GAP = 5;
// The zero line is placed so both sides of the plot have the same width; a
// short right side would make the same average look larger than its mirror.
const CENTRE = (PLOT_LEFT + PLOT_RIGHT) / 2;
const TOP = 34;
// A row holds its 12 px label on top and the bar pair below it, so the label
// never sits on the zero line and the rows do not touch.
const ROW_HEIGHT = 42;
const LABEL_OFFSET = 8;
const BAR_OFFSET = 24;
const BAR_HEIGHT = 12;
const TICK_HEIGHT = 8;
const BOTTOM = 22;

/** Mirror a plot coordinate across the zero line. */
function mirror(x: number): number {
  return 2 * CENTRE - x;
}

export function ProfileBars(props: ProfileBarsProps): ReactElement {
  const { forLine, againstLine, leagueLine, id } = props;
  const ids = chartIds(TITLE, id);

  const height = TOP + PROFILE_METRICS.length * ROW_HEIGHT + BOTTOM;

  return (
    <div className={styles.chartScroller}>
      <svg
        role="img"
        aria-labelledby={ids.labelledBy}
        viewBox={`0 0 ${WIDTH} ${height}`}
        className={styles.svg}
        style={{ width: `${WIDTH}px`, height: `${height}px` }}
        preserveAspectRatio="xMidYMid meet"
      >
        <title id={ids.titleId}>{TITLE}</title>
        <desc id={ids.descId}>{DESCRIPTION}</desc>
        <text
          x={(PLOT_LEFT + CENTRE) / 2}
          y={16}
          textAnchor="middle"
          className={styles.axisText}
        >
          Against
        </text>
        <text
          x={(CENTRE + PLOT_RIGHT) / 2}
          y={16}
          textAnchor="middle"
          className={styles.axisText}
        >
          For
        </text>
        {PROFILE_METRICS.map((metric, index) => {
          const rowTop = TOP + index * ROW_HEIGHT;
          const centreY = rowTop + BAR_OFFSET + BAR_HEIGHT / 2;
          const forValue = forLine[metric.key];
          const againstValue = againstLine[metric.key];
          const leagueValue = leagueLine[metric.key];
          // Each row is scaled to its own largest value, so goals and shots both
          // use the width they need and a short bar never reads as "nothing".
          const observed = [forValue, againstValue, leagueValue].filter(
            (value): value is number => value !== null,
          );
          const scale = linearScale(
            [0, niceMax(observed.reduce((max, value) => Math.max(max, value), 0))],
            [CENTRE, PLOT_RIGHT],
          );
          return (
            <g key={metric.key}>
              <text
                x={CENTRE}
                y={rowTop + LABEL_OFFSET}
                textAnchor="middle"
                dominantBaseline="central"
                className={styles.metricLabel}
              >
                {metric.label}
              </text>
              <line
                x1={CENTRE}
                y1={centreY - BAR_HEIGHT / 2 - 3}
                x2={CENTRE}
                y2={centreY + BAR_HEIGHT / 2 + 3}
                className={styles.axisLine}
              />
              {forValue === null ? null : (
                <rect
                  x={CENTRE}
                  y={centreY - BAR_HEIGHT / 2}
                  width={Math.max(0, scale.scale(forValue) - CENTRE)}
                  height={BAR_HEIGHT}
                  className={styles.forBar}
                />
              )}
              {againstValue === null ? null : (
                <rect
                  x={Math.min(mirror(scale.scale(againstValue)), CENTRE)}
                  y={centreY - BAR_HEIGHT / 2}
                  width={Math.max(0, CENTRE - mirror(scale.scale(againstValue)))}
                  height={BAR_HEIGHT}
                  className={styles.againstBar}
                />
              )}
              {leagueValue === null ? null : (
                <g className={styles.leagueTick}>
                  <line
                    x1={scale.scale(leagueValue)}
                    y1={centreY - TICK_HEIGHT}
                    x2={scale.scale(leagueValue)}
                    y2={centreY + TICK_HEIGHT}
                  />
                  <line
                    x1={mirror(scale.scale(leagueValue))}
                    y1={centreY - TICK_HEIGHT}
                    x2={mirror(scale.scale(leagueValue))}
                    y2={centreY + TICK_HEIGHT}
                  />
                </g>
              )}
              {forValue === null ? null : (
                <text
                  x={scale.scale(forValue) + VALUE_GAP}
                  y={centreY}
                  textAnchor="start"
                  dominantBaseline="central"
                  className={styles.barValue}
                >
                  {formatValue(forValue, 2)}
                </text>
              )}
              {againstValue === null ? null : (
                <text
                  x={mirror(scale.scale(againstValue)) - VALUE_GAP}
                  y={centreY}
                  textAnchor="end"
                  dominantBaseline="central"
                  className={styles.barValue}
                >
                  {formatValue(againstValue, 2)}
                </text>
              )}
            </g>
          );
        })}
        <text
          x={CENTRE}
          y={height - 6}
          textAnchor="middle"
          className={styles.axisText}
        >
          0
        </text>
      </svg>
    </div>
  );
}
