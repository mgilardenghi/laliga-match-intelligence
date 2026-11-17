/**
 * "Relegation race": how much danger the club is in, and what the table around
 * it looks like.
 *
 * The section reads two endpoints, in order: the race itself, which carries the
 * survival threshold, the nearest rivals, their stored forecast history, the
 * fixtures still to play and the identifier of the run it was built from, and
 * then that run with the projected points of every club. The run is read from
 * the identifier the race names, never from the season's latest one, because a
 * run that succeeded between two reads would otherwise pair new quantiles with
 * an older race. Neither is derived here: the section prints the stored numbers
 * and never re-runs a simulation.
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
  niceMax,
  type IntervalBarRow,
  type LineSeries,
} from "@/components/charts";
import { EmptyState, ProblemState } from "@/components/states";
import { MetricHelp } from "@/components/ui";
import {
  EM_DASH,
  formatCount,
  formatPercent,
  formatPoints,
} from "@/lib/format";
import { formatMadridDate } from "@/lib/madrid";

import {
  loadRace,
  loadSimulationDetail,
  type RelegationRace,
  type SimulationDetail,
  type TeamFocus,
} from "./data";
import { ordinal, qualificationLabels, type Qualification } from "./story";
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

/** How many runs the history chart needs before it is worth drawing. */
const HISTORY_MINIMUM_RUNS = 3;

/** How many of the remaining fixtures the section shows before the disclosure. */
const FIXTURES_SHOWN = 3;

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

  // The axis stops at 100 %, because a probability cannot pass it, and at a
  // round number just above the largest plotted value, so a race of small
  // chances is not flattened against the top of the plot.
  const plottedValues = series
    .flatMap((entry) => entry.values)
    .filter((value): value is number => value !== null);
  const top = Math.min(1, niceMax(Math.max(...plottedValues)));

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
      domain={[0, top]}
      series={series}
      note={`A point comes from the latest succeeded run of its as-of date, so the line is the series of final answers and not a series of attempts.${cut}`}
    />
  );
}

/**
 * The sentence the section shows instead of a history of one or two runs.
 *
 * A line through one point is a dot, and a line through two is a guess about
 * the trend between them: the section says how many runs are stored and leaves
 * the chart out until the series can show a change.
 */
function HistoryPending(props: { readonly stored: number }): ReactElement {
  const { stored } = props;
  return (
    <p className={styles.note}>
      {`The probability-over-time chart appears once three runs are stored; ${formatCount(stored)} stored so far.`}
    </p>
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
  readonly simulation: SimulationDetail;
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
        The stored simulation holds no row for this club or its rivals, so there
        are no projected points to plot. The threshold and the tables below
        still read from the stored run and the season.
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

/** The card heading and its definition, side by side and never nested. */
function CardHead(props: {
  readonly label: string;
  readonly definition: string;
}): ReactElement {
  const { label, definition } = props;
  return (
    <div className={styles.cardHead}>
      <h3 className={styles.cardTitle}>{label}</h3>
      <MetricHelp definition={definition} label={label} />
    </div>
  );
}

/** The rivals' stored numbers, one row per club of the race. */
function RivalsTable(props: {
  readonly rivals: readonly RaceRival[];
  readonly teamId: number;
  readonly season: string;
  readonly qualification: Qualification | null;
}): ReactElement {
  const { rivals, teamId, season, qualification } = props;

  if (rivals.length === 0) {
    return (
      <p className={styles.note}>
        The stored run holds no row for this club, so the rivals of the race
        cannot be named. The threshold and the remaining fixtures below read
        from the same stored run and the season.
      </p>
    );
  }

  const labels = qualificationLabels(qualification);

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
              Expected
            </th>
            <th
              className={`${styles.cell} ${styles.headCell} ${styles.numeric}`}
              scope="col"
            >
              {`P(${labels.relegation})`}
            </th>
          </tr>
        </thead>
        <tbody>
          {rivals.map((rival) => {
            const own = rival.team.id === teamId;
            return (
              <tr
                className={own ? styles.subjectRow : undefined}
                key={rival.team.id}
              >
                <th className={`${styles.cell} ${styles.rowHeader}`} scope="row">
                  {rival.team.name}
                </th>
                <td className={`${styles.cell} ${styles.numeric}`}>
                  {formatPoints(rival.expected_points)}
                </td>
                <td className={`${styles.cell} ${styles.numeric}`}>
                  {formatPercent(rival.p_relegation)}
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
 * The three probabilities of one remaining fixture.
 *
 * The contract declares the three fields nullable because the per-fixture
 * simulation is not stored yet; the row then says so in one cell instead of
 * printing three dashes that would read as a model that priced the fixture at
 * nothing.
 */
function FixtureProbabilities(props: {
  readonly fixture: RemainingFixture;
}): ReactElement {
  const { fixture } = props;
  if (
    fixture.p_win === null &&
    fixture.p_draw === null &&
    fixture.p_loss === null
  ) {
    return (
      <td className={styles.cell} colSpan={3}>
        model probabilities not available yet
      </td>
    );
  }
  return (
    <>
      <td className={`${styles.cell} ${styles.numeric}`}>
        {formatPercent(fixture.p_win)}
      </td>
      <td className={`${styles.cell} ${styles.numeric}`}>
        {formatPercent(fixture.p_draw)}
      </td>
      <td className={`${styles.cell} ${styles.numeric}`}>
        {formatPercent(fixture.p_loss)}
      </td>
    </>
  );
}

/** The fixture column: the opponent, and the day, venue and table place. */
function FixtureCell(props: {
  readonly fixture: RemainingFixture;
  readonly teamId: number;
}): ReactElement {
  const { fixture, teamId } = props;
  const match = fixture.match;
  const opponent =
    match.home_team.id === teamId ? match.away_team.name : match.home_team.name;
  const position =
    fixture.opponent_position === null
      ? EM_DASH
      : `${ordinal(fixture.opponent_position)} in table`;
  return (
    <th className={`${styles.cell} ${styles.rowHeader}`} scope="row">
      <span className={styles.fixtureName}>{opponent}</span>
      <span className={styles.fixtureMeta}>
        {`${formatMadridDate(match.kickoff_date)} · ${
          fixture.venue === "home" ? "Home" : "Away"
        } · ${position}`}
      </span>
    </th>
  );
}

/** One fixtures table: the next few, or all of them behind a disclosure. */
function FixturesTable(props: {
  readonly fixtures: readonly RemainingFixture[];
  readonly teamId: number;
  readonly caption: string;
}): ReactElement {
  const { fixtures, teamId, caption } = props;
  return (
    <div className={styles.tableWrap}>
      <table className={styles.table}>
        <caption className={styles.caption}>{caption}</caption>
        <thead>
          <tr>
            {["Fixture", "Win", "Draw", "Loss"].map((column, index) => (
              <th
                className={`${styles.cell} ${styles.headCell}${
                  index === 0 ? "" : ` ${styles.numeric}`
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
          {fixtures.map((fixture) => (
            <tr key={fixture.match.id}>
              <FixtureCell fixture={fixture} teamId={teamId} />
              <FixtureProbabilities fixture={fixture} />
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** The fixtures the club still has to play, with their model probabilities. */
function NextFixtures(props: {
  readonly fixtures: readonly RemainingFixture[];
  readonly teamId: number;
  readonly teamName: string;
  readonly season: string;
}): ReactElement {
  const { fixtures, teamId, teamName, season } = props;

  if (fixtures.length === 0) {
    return (
      <p className={styles.note}>
        The club has no further fixture in this season, so there is no run-in to
        price.
      </p>
    );
  }

  const shown = fixtures.slice(0, FIXTURES_SHOWN);
  const caption = `Remaining fixtures, ${season}: next ${formatCount(
    shown.length,
  )} of ${formatCount(fixtures.length)}`;

  return (
    <div className={styles.card}>
      <CardHead
        definition={`The model probability of each result of a remaining fixture, read from the club's side: a win is ${teamName} winning wherever it plays. The probabilities come from the latest succeeded simulation run of the season, so a fixture the run did not store has none.`}
        label="Next fixtures"
      />
      <FixturesTable
        caption={caption}
        fixtures={shown}
        teamId={teamId}
      />
      {fixtures.length > FIXTURES_SHOWN ? (
        <details className={styles.disclosure}>
          <summary className={styles.summary}>
            {`Show all ${formatCount(fixtures.length)} remaining fixtures`}
          </summary>
          <FixturesTable
            caption={`All remaining fixtures, ${season}`}
            fixtures={fixtures}
            teamId={teamId}
          />
        </details>
      ) : null}
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
  readonly simulation: SimulationDetail | null;
  readonly teamId: number;
  readonly teamName: string;
  readonly season: string;
  readonly outlook: TeamFocus["outlook"];
}): ReactElement {
  const { race, simulation, teamId, teamName, season, outlook } = props;
  const last = race.series.points[race.series.points.length - 1];
  const row = last === undefined ? undefined : pointRow(last, teamId);

  const pTitle = row?.p_title ?? outlook?.p_title;
  const pUcl = row?.p_ucl ?? outlook?.p_ucl;
  const pEurope = row?.p_europe ?? outlook?.p_europe;
  const qualification =
    simulation === null ? null : simulation.run.qualification;

  return (
    <>
      <p className={styles.note}>
        {`Not in the relegation race: no stored run put ${teamName} above ${formatPercent(COLLAPSED_BELOW)} to go down, so the section draws no probability line. Its stored outlook is P(title) ${formatPercent(pTitle)}, P(UCL) ${formatPercent(pUcl)} and P(Europe) ${formatPercent(pEurope)}.`}
      </p>
      <RivalsTable
        qualification={qualification}
        rivals={race.rivals}
        season={season}
        teamId={teamId}
      />
      <NextFixtures
        fixtures={race.remaining_fixtures}
        season={season}
        teamId={teamId}
        teamName={teamName}
      />
    </>
  );
}

/** The race itself: the histories, the projections and the two tables. */
function RaceBody(props: {
  readonly race: RelegationRace;
  readonly simulation: SimulationDetail;
  readonly teamId: number;
  readonly teamName: string;
  readonly season: string;
  readonly outlook: TeamFocus["outlook"];
}): ReactElement {
  const { race, simulation, teamId, teamName, season, outlook } = props;
  const points = race.series.points;
  const qualification = simulation.run.qualification;

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
        simulation={simulation}
        teamId={teamId}
        teamName={teamName}
      />
    );
  }

  return (
    <>
      <div className={styles.card}>
        <CardHead
          definition="The projected final points total of the clubs nearest the drop in the stored simulation. The thin bar spans the 5th to 95th percentile of simulated totals, the thick bar the interquartile range, the tick the median and the diamond the points already earned. The survival line is the median final total of the last club to stay up over the completed seasons."
          label="Projected finish"
        />
        <ProjectedPoints race={race} season={season} simulation={simulation} />
      </div>
      {points.length >= HISTORY_MINIMUM_RUNS ? (
        <RelegationHistory
          points={points}
          season={season}
          teamId={teamId}
          teamName={teamName}
        />
      ) : (
        <HistoryPending stored={points.length} />
      )}
      <RivalsTable
        qualification={qualification}
        rivals={race.rivals}
        season={season}
        teamId={teamId}
      />
      <NextFixtures
        fixtures={race.remaining_fixtures}
        season={season}
        teamId={teamId}
        teamName={teamName}
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

  // The race is read first because it names the run its numbers come from; the
  // projection has to be the table of that same run. Two parallel reads could
  // pair a new run's quantiles with an older race.
  const raceResult = await loadRace(teamId, season);

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
  } else if (raceResult.data.run_id === null) {
    // The race names no run exactly when the season holds no succeeded run, so
    // there is no projected table to draw. The section says so instead of
    // mounting a chart of an absence.
    body = (
      <EmptyState
        title={`No simulation stored for ${season}`}
        command="make simulate"
      >
        <p>
          The season has no succeeded simulation, so the race names no run and
          the section has no projected points to draw.
        </p>
      </EmptyState>
    );
  } else {
    // A failed read of the run the race names leaves the section in its failure
    // state; it never throws out of the section. A 404 here means the stored run
    // is gone, not that the season lacks one, so the season-level empty state
    // above would misdescribe it.
    const simulationResult = await loadSimulationDetail(raceResult.data.run_id);
    body = simulationResult.ok ? (
      <RaceBody
        outlook={outlook}
        race={raceResult.data}
        season={season}
        simulation={simulationResult.data}
        teamId={teamId}
        teamName={teamName}
      />
    ) : (
      <ProblemState problem={simulationResult.problem} />
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
