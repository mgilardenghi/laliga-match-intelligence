/**
 * "Multi-season trend": one sparkline per metric over every season the club
 * played.
 *
 * The section reads one endpoint. Every value is the API's own per-game average
 * for that season, so the page prints a number it was given and never averages
 * anything itself. A season the API records no value for is left out of the line
 * instead of being drawn as a zero, and the note says how many seasons carry the
 * metric, because a sparkline over the seasons that have a value is not a
 * sparkline over the club's whole history.
 *
 * The highlighted segment is the drop the API published for the metric: it
 * names the two seasons, and the section turns those codes into the indices of
 * the line it draws. The league position has no published drop, because a lower
 * position is a better finish and a fall down the table is not a fall of the
 * number, so its figure carries a caption instead.
 */
import type { ReactElement } from "react";

import { Sparkline, type SparklineHighlight } from "@/components/charts";
import { EmptyState, ProblemState } from "@/components/states";
import { formatCount, formatValue } from "@/lib/format";

import { loadHistory, type TeamHistory } from "./data";
import styles from "./page.module.css";

/** The label the `<Suspense>` boundary of this section shows while it reads. */
export const SECTION_LABEL = "Multi-season trend";

export type TrendSectionProps = {
  readonly teamId: number;
};

/** One season of the club's history, as the history endpoint serves it. */
type HistorySeason = TeamHistory["seasons"][number];

/** The steepest decline of one metric between two adjacent seasons. */
type HistoryDrop = TeamHistory["largest_drop"][number];

/** One sparkline of the grid: the field it reads, its label and its caption. */
type TrendMetric = {
  /** The payload key, used for the chart id and the drop lookup. */
  readonly key: string;
  /** The label the figure is titled with. */
  readonly label: string;
  /** The drop metric the API publishes for this field, or null when it has none. */
  readonly dropMetric: string | null;
  /** The caption used when the API published no drop for the field. */
  readonly fallbackNote: string;
  /** Read this field of one season, or null when the API did not record it. */
  readonly value: (season: HistorySeason) => number | null;
};

/**
 * The ten metrics of the grid, in the contract's order.
 *
 * The order is fixed here rather than read from the payload, so the grid does
 * not rearrange itself when a season happens to be missing a field.
 */
const METRICS: readonly TrendMetric[] = [
  {
    key: "position",
    label: "League position",
    // The API publishes no drop entry for the position: a lower number is a
    // better finish, so a fall down the table is not a fall of the value.
    dropMetric: null,
    fallbackNote:
      "League position at the end of each season; a lower number is a better finish.",
    value: (season) => season.position,
  },
  {
    key: "points_pg",
    label: "Points per match",
    dropMetric: "points_pg",
    fallbackNote: "Points per match of each season.",
    value: (season) => season.points_pg,
  },
  {
    key: "goals_for_pg",
    label: "Goals for per match",
    dropMetric: "goals_for_pg",
    fallbackNote: "Goals scored per match of each season.",
    value: (season) => season.goals_for_pg,
  },
  {
    key: "goals_against_pg",
    label: "Goals against per match",
    dropMetric: "goals_against_pg",
    fallbackNote: "Goals conceded per match of each season.",
    value: (season) => season.goals_against_pg,
  },
  {
    key: "shots_for_pg",
    label: "Shots for per match",
    dropMetric: "shots_for_pg",
    fallbackNote: "Shots per match of each season.",
    value: (season) => season.shots_for_pg,
  },
  {
    key: "shots_against_pg",
    label: "Shots against per match",
    dropMetric: "shots_against_pg",
    fallbackNote: "Shots faced per match of each season.",
    value: (season) => season.shots_against_pg,
  },
  {
    key: "sot_for_pg",
    label: "Shots on target for per match",
    dropMetric: "sot_for_pg",
    fallbackNote: "Shots on target per match of each season.",
    value: (season) => season.sot_for_pg,
  },
  {
    key: "sot_against_pg",
    label: "Shots on target against per match",
    dropMetric: "sot_against_pg",
    fallbackNote: "Shots on target faced per match of each season.",
    value: (season) => season.sot_against_pg,
  },
  {
    key: "cards_pg",
    label: "Cards per match",
    dropMetric: "cards_pg",
    fallbackNote: "Yellow and red cards per match of each season.",
    value: (season) => season.cards_pg,
  },
  {
    key: "xpts_pg",
    label: "Market xPts per match",
    dropMetric: "xpts_pg",
    fallbackNote:
      "Points the market expected per match of each season, from the pre-closing price of each fixture.",
    value: (season) => season.xpts_pg,
  },
];

/**
 * Turn a published drop into indices of the line actually drawn.
 *
 * The drop names two season codes; the figure draws only the seasons that carry
 * a value, so the codes are looked up in that shorter list. A drop whose
 * seasons are not both on the line yields no highlight rather than a segment
 * between the wrong two points.
 */
function highlightOf(
  drop: HistoryDrop,
  seasons: readonly string[],
): SparklineHighlight | undefined {
  const from = seasons.indexOf(drop.from_season);
  const to = seasons.indexOf(drop.to_season);
  return from >= 0 && to > from ? { from, to } : undefined;
}

/**
 * Say how many of the club's seasons carry the metric, when not all of them do.
 *
 * A field the API recorded in every season needs no such sentence, and one it
 * never recorded is stated instead of drawn. The wording is deliberately the
 * one the fixture-free note uses, so a reader can find the coverage of any
 * figure in the same place.
 */
function coverage(recorded: number, total: number): string {
  return recorded === total
    ? ""
    : ` The metric is recorded for ${formatCount(recorded)} of ${formatCount(total)} seasons, and the line joins only those.`;
}

/** One figure of the grid: a sparkline of one metric, with its caption. */
function TrendFigure(props: {
  readonly metric: TrendMetric;
  readonly history: TeamHistory;
}): ReactElement {
  const { metric, history } = props;

  const seasons: string[] = [];
  const values: number[] = [];
  for (const season of history.seasons) {
    const value = metric.value(season);
    if (value !== null && Number.isFinite(value)) {
      seasons.push(season.season);
      values.push(value);
    }
  }

  const total = history.seasons.length;
  const recorded = values.length;

  // A metric no season records has no line to draw; a chart of nothing would
  // read as a metric at zero.
  if (recorded === 0) {
    return (
      <p className={styles.note}>
        {`${metric.label}: no season of this club carries the value.`}
      </p>
    );
  }

  const drop =
    metric.dropMetric === null
      ? undefined
      : history.largest_drop.find(
          (entry) => entry.metric === metric.dropMetric,
        );

  const note =
    drop === undefined
      ? `${metric.fallbackNote}${coverage(recorded, total)}`
      : `${metric.label} fell most from ${drop.from_season} to ${drop.to_season} (${formatValue(drop.delta)}).${coverage(recorded, total)}`;

  return (
    <Sparkline
      id={`focus-trend-${metric.key}`}
      metric={metric.label}
      title={metric.label}
      description={`${metric.label} of ${history.team.name} in each of the ${formatCount(total)} seasons it played, oldest season first.`}
      seasons={seasons}
      values={values}
      highlight={drop === undefined ? undefined : highlightOf(drop, seasons)}
      note={note}
    />
  );
}

/** The grid of figures, for a club with at least one played season. */
function TrendBody(props: { readonly history: TeamHistory }): ReactElement {
  const { history } = props;

  return (
    <>
      <p className={styles.note}>
        {`One sparkline per metric over the ${formatCount(history.seasons.length)} seasons ${history.team.name} played, oldest first. Each line joins the seasons the API recorded a value for; the highlighted segment is the steepest drop the API published for that metric. A lower league position is a better finish, so the position line is read the other way round.`}
      </p>
      <div className={styles.trendGrid}>
        {METRICS.map((metric) => (
          <TrendFigure history={history} key={metric.key} metric={metric} />
        ))}
      </div>
    </>
  );
}

/**
 * The streamed section: the heading is rendered on every path, and the body is
 * decided by the read alone.
 */
export async function TrendSection(
  props: TrendSectionProps,
): Promise<ReactElement> {
  const { teamId } = props;
  const result = await loadHistory(teamId);

  return (
    <section className={styles.section} aria-labelledby="trend-heading">
      <h2 className={styles.heading} id="trend-heading">
        {SECTION_LABEL}
      </h2>
      {result.ok ? (
        result.data.seasons.length === 0 ? (
          <EmptyState title="No completed season to plot">
            <p>
              The API holds no played match of {result.data.team.name}, so there
              is no season, no average and no trend to draw.
            </p>
          </EmptyState>
        ) : (
          <TrendBody history={result.data} />
        )
      ) : result.status === 404 ? (
        <EmptyState title="No history for this club">
          <p>
            The API holds no club with this identifier, so there is no season
            history to plot.
          </p>
        </EmptyState>
      ) : (
        <ProblemState problem={result.problem} />
      )}
    </section>
  );
}
