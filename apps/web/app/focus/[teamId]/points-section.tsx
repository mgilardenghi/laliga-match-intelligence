/**
 * "Points against expectation": the club's season as a line against the market.
 *
 * The section reads one endpoint and renders it in its own state, so a rejected
 * or empty answer stays inside this section instead of taking the rest of the
 * page down. Both lines are drawn from the API's own match order: the cumulative
 * line is a sum over that order, and the rolling line is the per-match average
 * the API computed before each kickoff.
 */
import type { ReactElement } from "react";

import { LineChart, type LineSeries } from "@/components/charts";
import { SeasonSelect, type SeasonOption } from "@/components/shell";
import { EmptyState, ProblemState } from "@/components/states";
import { formatCount } from "@/lib/format";
import { formatMadridDate } from "@/lib/madrid";

import { loadSeasonStats, type TeamSeasonStats } from "./data";
import styles from "./page.module.css";

/** The label the `<Suspense>` boundary of this section shows while it reads. */
export const SECTION_LABEL = "Points against expectation";

export type PointsSectionProps = {
  readonly teamId: number;
  /** The season the page resolved, never the raw query value. */
  readonly season: string;
  /** The seasons the selector offers when this one has no data. */
  readonly seasonOptions: readonly SeasonOption[];
};

type CumulativeSeries = {
  /** One label per played match, in the API's own order. */
  readonly xLabels: readonly string[];
  /** Cumulative points, one point per played match. */
  readonly points: readonly number[];
  /** Cumulative market expected points, null until the first priced match. */
  readonly xpts: readonly (number | null)[];
  /** How many played matches carry a market price. */
  readonly pricedMatches: number;
};

/**
 * Sum points and market expected points over the played matches.
 *
 * A match the market did not price adds nothing to the expected-points total:
 * counting it as a zero forecast would invent an expectation nobody published.
 * The cumulative line is therefore null until the first priced match and flat
 * across later gaps, and the coverage below says how much of the season it
 * covers.
 */
function cumulativeSeries(
  matches: TeamSeasonStats["matches"],
): CumulativeSeries {
  const xLabels: string[] = [];
  const points: number[] = [];
  const xpts: (number | null)[] = [];
  let pointsTotal = 0;
  let xptsTotal = 0;
  let pricedMatches = 0;

  for (const line of matches) {
    xLabels.push(
      line.match.matchday === null
        ? formatMadridDate(line.match.kickoff_date)
        : `MD${line.match.matchday}`,
    );
    pointsTotal += line.points;
    points.push(pointsTotal);
    if (line.xpts !== null) {
      xptsTotal += line.xpts;
      pricedMatches += 1;
    }
    xpts.push(pricedMatches === 0 ? null : xptsTotal);
  }

  return { xLabels, points, xpts, pricedMatches };
}

/** The cumulative chart and the rolling chart, for a season with played matches. */
function PointsBody(props: {
  readonly data: TeamSeasonStats;
}): ReactElement {
  const { data } = props;
  const { xLabels, points, xpts, pricedMatches } = cumulativeSeries(
    data.matches,
  );

  const cumulative: readonly LineSeries[] = [
    { name: "Points", values: points, emphasis: true },
    { name: "Market xPts", values: xpts },
  ];

  // The four rolling averages the API computes over the five matches before each
  // kickoff. LineChart refuses more than five series, so the four fit.
  const rolling: readonly LineSeries[] = [
    {
      name: "Points per match",
      values: data.matches.map((line) => line.rolling5.points_pg),
      emphasis: true,
    },
    {
      name: "Market xPts per match",
      values: data.matches.map((line) => line.rolling5.xpts_pg),
    },
    {
      name: "Goals for per match",
      values: data.matches.map((line) => line.rolling5.goals_for_pg),
    },
    {
      name: "Goals against per match",
      values: data.matches.map((line) => line.rolling5.goals_against_pg),
    },
  ];

  return (
    <>
      <LineChart
        id="focus-cumulative-points"
        title="Cumulative points against market expectation"
        description={`Points the club earned and points the market expected, summed over the ${formatCount(data.matches.length)} played matches of the ${data.season} season in kickoff order.`}
        xLabels={xLabels}
        yLabel="Points"
        series={cumulative}
        note={
          <>
            Market expected points for {formatCount(pricedMatches)} of{" "}
            {formatCount(data.matches.length)} played matches, from the
            pre-closing price of each one; a match without a price adds nothing
            to that line, so a gap in it is a gap in the coverage and not a
            forecast of zero. Above the line = results better than the market
            expected.
          </>
        }
      />
      <LineChart
        id="focus-rolling-form"
        title="Rolling five-match averages"
        description="Points, market expected points and goals for and against, each averaged over the at most five matches the club played on a date strictly earlier than the match the point belongs to. The first matches of the season have no average yet."
        xLabels={xLabels}
        yLabel="Per match"
        series={rolling}
        note="Every series is a per-match average, so the unit of all four lines is per match. A point is missing where the window holds no match, which is the normal state of the first matches of the season."
      />
    </>
  );
}

/**
 * The streamed section: the heading is rendered on every path, and the body is
 * decided by the read alone.
 */
export async function PointsSection(
  props: PointsSectionProps,
): Promise<ReactElement> {
  const { teamId, season, seasonOptions } = props;
  const result = await loadSeasonStats(teamId, season);

  return (
    <section className={styles.section} aria-labelledby="points-heading">
      <h2 className={styles.heading} id="points-heading">
        {SECTION_LABEL}
      </h2>
      {result.ok ? (
        result.data.matches.length === 0 ? (
          <EmptyState title={`No played matches in ${result.data.season}`}>
            <p>
              The API holds no played match of this club in this season, so
              there is no points line to draw against the market.
            </p>
          </EmptyState>
        ) : (
          <PointsBody data={result.data} />
        )
      ) : result.status === 404 ? (
        <EmptyState
          title={`No season statistics for ${season}`}
          action={
            <SeasonSelect
              options={seasonOptions}
              value={season}
              path={`/focus/${teamId}`}
            />
          }
        >
          <p>
            The API holds no statistics for this club in this season. Pick
            another season from the list.
          </p>
        </EmptyState>
      ) : (
        <ProblemState problem={result.problem} />
      )}
    </section>
  );
}
