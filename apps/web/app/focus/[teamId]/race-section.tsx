/**
 * "Relegation race": how much danger the club is in, and what the table around
 * it looks like.
 *
 * The section reads two endpoints: the race itself, which carries the survival
 * threshold, the nearest rivals, their stored forecast history and the fixtures
 * still to play, and the latest succeeded simulation, which carries the
 * projected points of every club. Neither is derived here: the section prints
 * the stored numbers and never re-runs a simulation.
 *
 * A club that the stored runs never put near the drop is not shown a chart of
 * a flat zero. Its race collapses to a sentence and the two tables, because the
 * point of the section is a risk that exists, and the collapse rule keeps the
 * page from dressing an absence of risk as a measurement.
 */
import type { ReactElement } from "react";

import {
  IntervalBars,
  LineChart,
  type IntervalBarRow,
  type LineSeries,
} from "@/components/charts";
import { EmptyState, ProblemState } from "@/components/states";
import {
  EM_DASH,
  formatCount,
  formatPercent,
  formatPoints,
} from "@/lib/format";
import { formatMadridDate } from "@/lib/madrid";

import {
  loadLatestSimulation,
  loadRace,
  type LatestSimulation,
  type RelegationRace,
  type TeamFocus,
} from "./data";
import styles from "./page.module.css";

/** The label the `<Suspense>` boundary of this section shows while it reads. */
export const SECTION_LABEL = "Relegation race";

/**
 * The probability below which the race is over for the club.
 *
 * The runs of a club that never fell into the drop are all exactly zero, so a
 * line of zeros would draw a risk the simulation never saw. The threshold is
 * read against the club's own worst stored run, not against its latest one.
 */
const COLLAPSED_BELOW = 0.01;

export type RaceSectionProps = {
  readonly teamId: number;
  /** The season the page resolved, never the raw query value. */
  readonly season: string;
  readonly teamName: string;
  /** The focus payload's outlook, null when the season has no succeeded run. */
  readonly outlook: TeamFocus["outlook"];
};

/** One point of the stored forecast history. */
type HistoryPoint = RelegationRace["series"]["points"][number];

/** One club of the race, as the race endpoint serves it. */
type RaceRival = RelegationRace["rivals"][number];

/** One fixture the club still has to play. */
type RemainingFixture = RelegationRace["remaining_fixtures"][number];

/** Return `value` with its English ordinal suffix (`17` is `17th`). */
function ordinal(value: number): string {
  const tens = value % 100;
  if (tens >= 11 && tens <= 13) {
    return `${formatCount(value)}th`;
  }
  const units = value % 10;
  if (units === 1) {
    return `${formatCount(value)}st`;
  }
  if (units === 2) {
    return `${formatCount(value)}nd`;
  }
  if (units === 3) {
    return `${formatCount(value)}rd`;
  }
  return `${formatCount(value)}th`;
}

/** The subject club's row of one history point, or undefined when it is absent. */
function pointRow(
  point: HistoryPoint,
  teamId: number,
): HistoryPoint["teams"][number] | undefined {
  return point.teams.find((entry) => entry.team.id === teamId);
}

/** The survival threshold, with the sample it was read from. */
function thresholdNote(threshold: RelegationRace["survival_threshold"]): string {
  return `Survival threshold: median ${ordinal(threshold.position)}-place total over ${formatCount(threshold.seasons_used)} completed seasons since 2005-06; the lowest was ${formatPoints(threshold.points_min)} and the highest ${formatPoints(threshold.points_max)} points.`;
}

/**
 * The relegation probability of the group over the stored runs.
 *
 * The clubs are the ones the last stored run holds, the subject club first so
 * it is always drawn: a chart of five clubs is readable, and the contract of
 * the figure stops there, so the note names how many of the run's clubs it
 * shows when the run holds more.
 */
function RelegationHistory(props: {
  readonly points: readonly HistoryPoint[];
  readonly teamId: number;
  readonly teamName: string;
  readonly season: string;
}): ReactElement {
  const { points, teamId, teamName, season } = props;
  const last = points[points.length - 1];
  const subject = pointRow(last, teamId);
  const others = last.teams.filter((entry) => entry.team.id !== teamId);
  const plotted =
    subject === undefined
      ? others.slice(0, 5)
      : [subject, ...others.slice(0, 4)];

  // A run that mentions no club at all has no line to draw; an empty chart
  // would look like a race of nothing rather than a run without rows.
  if (plotted.length === 0) {
    return (
      <p className={styles.note}>
        The last stored run holds no club of this race, so there is no
        probability history to draw. The threshold and the tables below read
        from the same stored run and the season.
      </p>
    );
  }

  const series: readonly LineSeries[] = plotted.map((entry) => ({
    name: entry.team.name,
    emphasis: entry.team.id === teamId,
    values: points.map((point) => {
      const row = pointRow(point, entry.team.id);
      return row === undefined ? null : row.p_relegation;
    }),
  }));

  const singleRun =
    points.length === 1
      ? " One run is stored so far, so the history is a single point; it grows as further runs are stored."
      : "";
  const cut =
    plotted.length < last.teams.length
      ? ` The chart draws ${formatCount(plotted.length)} of the ${formatCount(last.teams.length)} clubs of the run: ${teamName} and the first four others.`
      : "";

  return (
    <LineChart
      id="focus-relegation-history"
      title={`Relegation probability over the stored runs, ${season}`}
      description={`The stored relegation probability of each club of the last run, one point per as-of date of a succeeded simulation of the ${season} season, in date order.`}
      xLabels={points.map((point) => point.as_of_date)}
      yLabel="P(relegation)"
      series={series}
      note={`A point comes from the latest succeeded run of its as-of date, so the line is the series of final answers and not a series of attempts.${singleRun}${cut}`}
    />
  );
}

/**
 * The projected final points of the race, against the survival line.
 *
 * The rows are the race's own clubs, so the chart and the rivals table cannot
 * list different groups; a club the run's table does not hold is skipped, and
 * the note says how many of the raced clubs were drawn.
 */
function ProjectedPoints(props: {
  readonly race: RelegationRace;
  readonly simulation: LatestSimulation;
  readonly season: string;
}): ReactElement {
  const { race, simulation, season } = props;
  const rows: IntervalBarRow[] = [];
  for (const rival of race.rivals) {
    const row = simulation.table.find(
      (entry) => entry.team.id === rival.team.id,
    );
    if (row === undefined) {
      continue;
    }
    rows.push({
      team: rival.team.name,
      p05: row.points_quantiles.p05,
      p25: row.points_quantiles.p25,
      p50: row.points_quantiles.p50,
      p75: row.points_quantiles.p75,
      p95: row.points_quantiles.p95,
      current: row.current.points,
    });
  }

  if (rows.length === 0) {
    return (
      <p className={styles.note}>
        The stored simulation holds no row for this club or its rivals, so
        there are no projected points to plot. The threshold and the tables
        below still read from the stored run and the season.
      </p>
    );
  }

  return (
    <IntervalBars
      id="focus-projections"
      title={`Projected final points, ${season}`}
      description={`The projected final total of the ${formatCount(rows.length)} clubs of the race: the thin bar spans the 5th to 95th percentile, the thick bar the interquartile range, the tick the median and the diamond the points already earned.`}
      rows={rows}
      threshold={race.survival_threshold.points_median}
      note={`${thresholdNote(race.survival_threshold)} Plotted ${formatCount(rows.length)} of the ${formatCount(race.rivals.length)} clubs of the race.`}
    />
  );
}

/** The rivals' stored numbers, one row per club of the race. */
function RivalsTable(props: {
  readonly rivals: readonly RaceRival[];
  readonly teamId: number;
  readonly season: string;
}): ReactElement {
  const { rivals, teamId, season } = props;

  if (rivals.length === 0) {
    return (
      <p className={styles.note}>
        The stored run holds no row for this club, so the rivals of the race
        cannot be named. The threshold and the remaining fixtures below read
        from the same stored run and the season.
      </p>
    );
  }

  return (
    <div className={styles.tableWrap}>
      <table className={styles.table}>
        <caption className={styles.caption}>
          {`Rivals and their relegation probability, ${season}`}
        </caption>
        <thead>
          <tr>
            <th className={`${styles.cell} ${styles.headCell}`} scope="col">
              Club
            </th>
            <th
              className={`${styles.cell} ${styles.headCell} ${styles.numeric}`}
              scope="col"
            >
              Current points
            </th>
            <th
              className={`${styles.cell} ${styles.headCell} ${styles.numeric}`}
              scope="col"
            >
              Expected points
            </th>
            <th
              className={`${styles.cell} ${styles.headCell} ${styles.numeric}`}
              scope="col"
            >
              P(relegation)
            </th>
            <th
              className={`${styles.cell} ${styles.headCell} ${styles.numeric}`}
              scope="col"
            >
              Projected points p05-p95
            </th>
          </tr>
        </thead>
        <tbody>
          {rivals.map((rival) => (
            <tr key={rival.team.id}>
              <th className={`${styles.cell} ${styles.rowHeader}`} scope="row">
                {rival.team.id === teamId
                  ? `${rival.team.name} (this club)`
                  : rival.team.name}
              </th>
              <td className={`${styles.cell} ${styles.numeric}`}>
                {formatCount(rival.current_points)}
              </td>
              <td className={`${styles.cell} ${styles.numeric}`}>
                {formatPoints(rival.expected_points)}
              </td>
              <td className={`${styles.cell} ${styles.numeric}`}>
                {formatPercent(rival.p_relegation)}
              </td>
              <td className={`${styles.cell} ${styles.numeric}`}>
                {`${formatPoints(rival.points_p05)}–${formatPoints(rival.points_p95)}`}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * The probabilities of one remaining fixture.
 *
 * The contract declares the three fields nullable because the per-fixture
 * simulation is not stored yet; the cell says so rather than printing three
 * dashes that would read as a model that priced the fixture at nothing.
 */
function fixtureProbabilities(fixture: RemainingFixture): string {
  if (
    fixture.p_win === null &&
    fixture.p_draw === null &&
    fixture.p_loss === null
  ) {
    return "model probabilities not available yet";
  }
  return `W ${formatPercent(fixture.p_win)}, D ${formatPercent(fixture.p_draw)}, L ${formatPercent(fixture.p_loss)}`;
}

/** The fixtures the club still has to play, with what is known about each. */
function FixturesTable(props: {
  readonly fixtures: readonly RemainingFixture[];
  readonly teamId: number;
  readonly season: string;
}): ReactElement {
  const { fixtures, teamId, season } = props;

  if (fixtures.length === 0) {
    return (
      <p className={styles.note}>
        The club has no further fixture in this season, so there is no run-in to
        price.
      </p>
    );
  }

  return (
    <div className={styles.tableWrap}>
      <table className={styles.table}>
        <caption className={styles.caption}>
          {`Remaining fixtures, ${season}`}
        </caption>
        <thead>
          <tr>
            {[
              "Date",
              "Matchday",
              "Opponent",
              "Venue",
              "Opponent position",
              "Model probabilities",
            ].map((column, index) => (
              <th
                className={`${styles.cell} ${styles.headCell}${
                  index === 1 || index === 4 ? ` ${styles.numeric}` : ""
                }`}
                key={column}
                scope="col"
              >
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {fixtures.map((fixture) => {
            const match = fixture.match;
            const opponent =
              match.home_team.id === teamId
                ? match.away_team.name
                : match.home_team.name;
            return (
              <tr key={match.id}>
                <td className={styles.cell}>
                  {formatMadridDate(match.kickoff_date)}
                </td>
                <td className={`${styles.cell} ${styles.numeric}`}>
                  {formatCount(match.matchday)}
                </td>
                <td className={styles.cell}>{opponent}</td>
                <td className={styles.cell}>
                  {fixture.venue === "home" ? "Home" : "Away"}
                </td>
                <td className={`${styles.cell} ${styles.numeric}`}>
                  {fixture.opponent_position === null
                    ? EM_DASH
                    : formatCount(fixture.opponent_position)}
                </td>
                <td className={styles.cell}>
                  {fixtureProbabilities(fixture)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/**
 * What the section says instead of the charts when the club is not in the race.
 *
 * The three probabilities are the club's row of the last stored point, or the
 * focus payload's outlook when that point does not mention the club. A value
 * neither carries is an em dash: the section has no other source, and a zero
 * would be a claim about a risk nobody simulated.
 */
function CollapsedRace(props: {
  readonly race: RelegationRace;
  readonly teamId: number;
  readonly teamName: string;
  readonly season: string;
  readonly outlook: TeamFocus["outlook"];
}): ReactElement {
  const { race, teamId, teamName, season, outlook } = props;
  const last = race.series.points[race.series.points.length - 1];
  const row = last === undefined ? undefined : pointRow(last, teamId);

  const pTitle = row?.p_title ?? outlook?.p_title;
  const pUcl = row?.p_ucl ?? outlook?.p_ucl;
  const pEurope = row?.p_europe ?? outlook?.p_europe;

  return (
    <>
      <p className={styles.note}>
        {`Not in the relegation race: no stored run put ${teamName} above ${formatPercent(COLLAPSED_BELOW)} to go down, so the section draws no probability line. Its stored outlook is P(title) ${formatPercent(pTitle)}, P(UCL) ${formatPercent(pUcl)} and P(Europe) ${formatPercent(pEurope)}.`}
      </p>
      <RivalsTable rivals={race.rivals} season={season} teamId={teamId} />
      <FixturesTable
        fixtures={race.remaining_fixtures}
        season={season}
        teamId={teamId}
      />
    </>
  );
}

/** The race itself: the histories, the projections and the two tables. */
function RaceBody(props: {
  readonly race: RelegationRace;
  readonly simulation: LatestSimulation;
  readonly teamId: number;
  readonly teamName: string;
  readonly season: string;
  readonly outlook: TeamFocus["outlook"];
}): ReactElement {
  const { race, simulation, teamId, teamName, season, outlook } = props;
  const points = race.series.points;

  if (points.length === 0) {
    return (
      <EmptyState title={`No simulation stored for ${season}`} command="make simulate">
        <p>
          The season has no succeeded simulation, so the race has no stored
          probability to draw and no projected table to read.
        </p>
      </EmptyState>
    );
  }

  // The club's own worst stored probability decides the collapse, so a run that
  // once showed real danger keeps the charts even if the latest one shows none.
  const stored = points
    .map((point) => pointRow(point, teamId)?.p_relegation)
    .filter((value): value is number => value !== undefined && Number.isFinite(value));
  const maxP =
    stored.length > 0
      ? Math.max(...stored)
      : (outlook === null ? null : outlook.p_relegation);
  const collapsed = maxP !== null && maxP < COLLAPSED_BELOW;

  if (collapsed) {
    return (
      <CollapsedRace
        outlook={outlook}
        race={race}
        season={season}
        teamId={teamId}
        teamName={teamName}
      />
    );
  }

  return (
    <>
      <RelegationHistory
        points={points}
        season={season}
        teamId={teamId}
        teamName={teamName}
      />
      <ProjectedPoints race={race} season={season} simulation={simulation} />
      <RivalsTable rivals={race.rivals} season={season} teamId={teamId} />
      <FixturesTable
        fixtures={race.remaining_fixtures}
        season={season}
        teamId={teamId}
      />
    </>
  );
}

/**
 * The streamed section: the heading is rendered on every path, and the body is
 * decided by the reads alone.
 */
export async function RaceSection(
  props: RaceSectionProps,
): Promise<ReactElement> {
  const { teamId, season, teamName, outlook } = props;

  const [raceResult, simulationResult] = await Promise.all([
    loadRace(teamId, season),
    loadLatestSimulation(season),
  ]);

  let body: ReactElement;
  if (!raceResult.ok) {
    body =
      raceResult.status === 404 ? (
        <EmptyState title={`No relegation race for ${season}`}>
          <p>
            The API holds no race for this club and season: the club may not play
            it, or the season may not be stored. No threshold and no fixture is
            drawn from an answer the API refused.
          </p>
        </EmptyState>
      ) : (
        <ProblemState problem={raceResult.problem} />
      );
  } else if (!simulationResult.ok) {
    body =
      simulationResult.status === 404 ? (
        <EmptyState
          title={`No simulation stored for ${season}`}
          command="make simulate"
        >
          <p>
            The season has no succeeded simulation, so the projected points of
            the race have no stored run to read.
          </p>
        </EmptyState>
      ) : (
        <ProblemState problem={simulationResult.problem} />
      );
  } else {
    body = (
      <RaceBody
        outlook={outlook}
        race={raceResult.data}
        season={season}
        simulation={simulationResult.data}
        teamId={teamId}
        teamName={teamName}
      />
    );
  }

  return (
    <section className={styles.section} aria-labelledby="race-heading">
      <h2 className={styles.heading} id="race-heading">
        {SECTION_LABEL}
      </h2>
      {body}
    </section>
  );
}
