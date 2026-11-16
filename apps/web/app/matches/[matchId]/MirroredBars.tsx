/**
 * The mirrored per-match profile of one side, against the league average.
 *
 * The shared chart directory has no mirrored-bar component and this task does
 * not own it, so the chart lives beside its route and wraps `ChartFigure`: every
 * chart on the dashboard is a figure with a caption, an `svg[role="img"]`
 * labelled by its title and description, and a disclosure holding the exact
 * numbers. The exact values live in that table; the drawing encodes direction
 * and size, and the league average is a tick on each row, so a reader who cannot
 * separate the two fills still gets the comparison without colour.
 */
import type { ReactElement, ReactNode } from "react";

import { ChartFigure, chartIds, linearScale, niceMax } from "@/components/charts";
import { formatValue } from "@/lib/format";

import styles from "./page.module.css";

/** One metric of the profile: this side, its opponents, and the league. */
export type MirroredRow = {
  readonly label: string;
  readonly own: number | null;
  readonly against: number | null;
  readonly league: number | null;
};

export type MirroredBarsProps = {
  readonly title: string;
  readonly description: string;
  readonly rows: readonly MirroredRow[];
  /** What the right-hand bar is, for the axis label and the note. */
  readonly ownLabel: string;
  /** What the left-hand bar is. */
  readonly againstLabel: string;
  readonly id?: string;
  readonly note?: ReactNode;
};

// The drawing is narrow enough for a phone: the metric label sits above its
// row instead of beside it, so the mirrored pair fits in 300 px and nothing hides
// behind a sideways scroll. The values print at the outer end of each bar, so
// both sides keep a lane of their own that holds a value such as "12.34" at
// 12 px without running past the drawing.
const WIDTH = 300;
const PLOT_LEFT = 46;
const PLOT_RIGHT = 254;
const VALUE_GAP = 5;
const CENTRE = (PLOT_LEFT + PLOT_RIGHT) / 2;
const HALF_WIDTH = CENTRE - PLOT_LEFT;
// A row holds its 12 px label on top and the bar pair below it.
const ROW_HEIGHT = 40;
const LABEL_OFFSET = 8;
const BAR_OFFSET = 18;
const BAR_HEIGHT = 14;
const TOP = 26;
const BOTTOM = 10;

function value(row: MirroredRow): number {
  return Math.max(row.own ?? 0, row.against ?? 0, row.league ?? 0);
}

/**
 * Draw the profile.
 *
 * Each row carries its own scale, because the rows count different things
 * (goals and shots are not comparable) and the comparison a reader makes is
 * within one row: this side against its opponents and against the league. The
 * note says so, so nobody reads two rows as one scale.
 */
export function MirroredBars(props: MirroredBarsProps): ReactElement {
  const { title, description, rows, ownLabel, againstLabel, id, note } = props;
  const ids = chartIds(id ?? title);
  const height = TOP + rows.length * ROW_HEIGHT + BOTTOM;

  return (
    <ChartFigure
      title={title}
      description={description}
      table={{
        caption: `${title}: per-match averages`,
        columns: ["Metric", ownLabel, againstLabel, "League average"],
        rows: rows.map((row) => [
          row.label,
          formatValue(row.own, 2),
          formatValue(row.against, 2),
          formatValue(row.league, 2),
        ]),
      }}
      note={note}
    >
      <div className={styles.chartScroller}>
          <svg
            role="img"
            aria-labelledby={ids.labelledBy}
            viewBox={`0 0 ${WIDTH} ${height}`}
            className={styles.chartSvg}
            style={{ width: `${WIDTH}px`, height: `${height}px` }}
            preserveAspectRatio="xMidYMid meet"
          >
            <title id={ids.titleId}>{title}</title>
            <desc id={ids.descId}>{description}</desc>
            <text
              x={0}
              y={14}
              textAnchor="start"
              className={styles.chartAxis}
            >
              {againstLabel}
            </text>
            <text x={WIDTH} y={14} textAnchor="end" className={styles.chartAxis}>
              {ownLabel}
            </text>
            {rows.map((row, index) => {
              const rowTop = TOP + index * ROW_HEIGHT;
              const y = rowTop + BAR_OFFSET;
              const centreY = y + BAR_HEIGHT / 2;
              // Each row is scaled to its own largest value, so a row of goals and
              // a row of shots both use the width they need.
              const scale = linearScale([0, niceMax(value(row))], [0, HALF_WIDTH]);
              const ownWidth = row.own === null ? 0 : scale.scale(row.own);
              const againstWidth = row.against === null ? 0 : scale.scale(row.against);
              const leagueX = row.league === null ? null : CENTRE - scale.scale(row.league);
              return (
                <g key={row.label}>
                  <text
                    x={CENTRE}
                    y={rowTop + LABEL_OFFSET}
                    textAnchor="middle"
                    dominantBaseline="central"
                    className={styles.chartLabel}
                  >
                    {row.label}
                  </text>
                  <line
                    x1={CENTRE}
                    y1={y - 3}
                    x2={CENTRE}
                    y2={y + BAR_HEIGHT + 3}
                    className={styles.chartAxisLine}
                  />
                  {againstWidth > 0 ? (
                    <rect
                      x={CENTRE - againstWidth}
                      y={y}
                      width={againstWidth}
                      height={BAR_HEIGHT}
                      rx={2}
                      className={styles.chartAgainst}
                    />
                  ) : null}
                  {ownWidth > 0 ? (
                    <rect
                      x={CENTRE}
                      y={y}
                      width={ownWidth}
                      height={BAR_HEIGHT}
                      rx={2}
                      className={styles.chartOwn}
                    />
                  ) : null}
                  {leagueX === null ? null : (
                    <line
                      x1={leagueX}
                      y1={y - 2}
                      x2={leagueX}
                      y2={y + BAR_HEIGHT + 2}
                      className={styles.chartLeague}
                    />
                  )}
                  {/* The values come last so a league marker that happens to
                      sit beside a value is drawn under it rather than through
                      it; the number stays legible where the two meet. */}
                  {row.own === null ? null : (
                    <text
                      x={CENTRE + ownWidth + VALUE_GAP}
                      y={centreY}
                      textAnchor="start"
                      dominantBaseline="central"
                      className={styles.chartValue}
                    >
                      {formatValue(row.own, 2)}
                    </text>
                  )}
                  {row.against === null ? null : (
                    <text
                      x={CENTRE - againstWidth - VALUE_GAP}
                      y={centreY}
                      textAnchor="end"
                      dominantBaseline="central"
                      className={styles.chartValue}
                    >
                      {formatValue(row.against, 2)}
                    </text>
                  )}
                </g>
              );
            })}
          </svg>
      </div>
    </ChartFigure>
  );
}
