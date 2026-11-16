/**
 * "Next match": the club's next fixture, priced, with the opponent in context.
 *
 * The section renders from three sources: the focus payload's `upcoming` list,
 * which the page already holds, the opponent's season statistics, and the
 * relegation race, which carries the next-match swing. The swing is declared in
 * the contract but not stored yet, so the panel says so instead of printing a
 * number nobody computed.
 *
 * A refused opponent profile is a short note, not a failure of the section: the
 * fixture, its stored forecasts and the swing panel are still worth reading
 * without it.
 */
import Link from "next/link";
import type { ReactElement } from "react";

import { ProbabilityBar } from "@/components/charts";
import { EmptyState, ProblemState } from "@/components/states";
import {
  EM_DASH,
  formatPercent,
  formatSigned,
  formatValue,
} from "@/lib/format";
import { modelLabel } from "@/lib/labels";
import { formatMadridDate, formatMadridDateTime } from "@/lib/madrid";

import {
  loadRace,
  loadSeasonStats,
  type RelegationRace,
  type TeamFocus,
  type TeamSeasonStats,
} from "./data";
import styles from "./page.module.css";

/** The label the `<Suspense>` boundary of this section shows while it reads. */
export const SECTION_LABEL = "Next match";

/**
 * What the swing panel says while the contract carries no stored swing.
 *
 * The sentence is the whole answer: the fields exist so a run can fill them
 * later, and a placeholder number would read as a stored simulation output.
 */
const SWING_PENDING =
  "available after the next simulation run with swing support";

export type NextMatchSectionProps = {
  readonly teamId: number;
  /** The season the page resolved, never the raw query value. */
  readonly season: string;
  readonly teamName: string;
  /** The focus payload's next fixtures; the section reads the first one. */
  readonly upcoming: TeamFocus["upcoming"];
};

/** One fixture still to be played, with the stored forecasts of it. */
type UpcomingMatch = TeamFocus["upcoming"][number];

/** One number of the opponent profile: its own average and the league's. */
type ComparisonRow = {
  readonly label: string;
  readonly own: number | null;
  readonly league: number | null;
};

/**
 * The opponent's per-match numbers next to the league average.
 *
 * Both columns are the API's own averages over the same season, so the table
 * compares two published numbers and never divides anything itself. A field no
 * counted match carries is an em dash, not a zero.
 */
function OpponentProfile(props: {
  readonly opponentName: string;
  readonly data: TeamSeasonStats;
}): ReactElement {
  const { opponentName, data } = props;
  const rows: readonly ComparisonRow[] = [
    {
      label: "Goals",
      own: data.per_match.for.goals,
      league: data.league_avg.goals,
    },
    {
      label: "Shots",
      own: data.per_match.for.shots,
      league: data.league_avg.shots,
    },
    {
      label: "Shots on target",
      own: data.per_match.for.shots_on_target,
      league: data.league_avg.shots_on_target,
    },
    {
      label: "Fouls",
      own: data.per_match.for.fouls,
      league: data.league_avg.fouls,
    },
    {
      label: "Yellow cards",
      own: data.per_match.for.yellow,
      league: data.league_avg.yellow,
    },
  ];

  return (
    <div className={styles.tableWrap}>
      <table className={styles.table}>
        <caption className={styles.caption}>
          {`${opponentName} per match against the league average, ${data.season} season`}
        </caption>
        <thead>
          <tr>
            <th className={`${styles.cell} ${styles.headCell}`} scope="col">
              Statistic
            </th>
            <th
              className={`${styles.cell} ${styles.headCell} ${styles.numeric}`}
              scope="col"
            >
              {opponentName}
            </th>
            <th
              className={`${styles.cell} ${styles.headCell} ${styles.numeric}`}
              scope="col"
            >
              League average
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.label}>
              <th className={`${styles.cell} ${styles.rowHeader}`} scope="row">
                {row.label}
              </th>
              <td className={`${styles.cell} ${styles.numeric}`}>
                {formatValue(row.own)}
              </td>
              <td className={`${styles.cell} ${styles.numeric}`}>
                {formatValue(row.league)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** One outcome of the next match, as the swing payload carries it. */
type SwingOutcome = NonNullable<RelegationRace["next_match"]>["outcomes"]["win"];

/**
 * What each outcome of the next match would do to the club's season.
 *
 * Every number is a field of the payload; the change columns subtract the
 * club's stored current probability from the projection of that outcome, which
 * is the comparison the panel exists for. The current relegation probability is
 * the rival row's, and the current Europe probability comes from the last
 * stored point, because that is where each one is published. A club with no
 * stored current value gets an em dash rather than a change against an invented
 * baseline.
 */
function SwingTable(props: {
  readonly race: RelegationRace;
  readonly teamId: number;
  readonly season: string;
}): ReactElement {
  const { race, teamId, season } = props;
  const swing = race.next_match;
  if (swing === null) {
    return <p className={styles.note}>{SWING_PENDING}</p>;
  }

  const lastPoint = race.series.points[race.series.points.length - 1];
  const subjectRow = lastPoint?.teams.find(
    (entry) => entry.team.id === teamId,
  );
  const currentRelegation =
    race.rivals.find((rival) => rival.team.id === teamId)?.p_relegation ??
    subjectRow?.p_relegation;
  const currentEurope = subjectRow?.p_europe;
  const outcomes: readonly { label: string; outcome: SwingOutcome }[] = [
    { label: "Win", outcome: swing.outcomes.win },
    { label: "Draw", outcome: swing.outcomes.draw },
    { label: "Loss", outcome: swing.outcomes.loss },
  ];

  const change = (next: number, now: number | undefined): string =>
    now === undefined ? EM_DASH : `${formatSigned((next - now) * 100)} pp`;

  return (
    <div className={styles.tableWrap}>
      <table className={styles.table}>
        <caption className={styles.caption}>
          {`Swing of the next match, ${season}`}
        </caption>
        <thead>
          <tr>
            {[
              "Outcome",
              "P(outcome)",
              "P(relegation)",
              "Change in P(relegation)",
              "P(Europe)",
              "Change in P(Europe)",
            ].map((column, index) => (
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
          {outcomes.map((row) => (
            <tr key={row.label}>
              <th className={`${styles.cell} ${styles.rowHeader}`} scope="row">
                {row.label}
              </th>
              <td className={`${styles.cell} ${styles.numeric}`}>
                {formatPercent(row.outcome.p_outcome)}
              </td>
              <td className={`${styles.cell} ${styles.numeric}`}>
                {formatPercent(row.outcome.p_relegation)}
              </td>
              <td className={`${styles.cell} ${styles.numeric}`}>
                {change(row.outcome.p_relegation, currentRelegation)}
              </td>
              <td className={`${styles.cell} ${styles.numeric}`}>
                {formatPercent(row.outcome.p_europe)}
              </td>
              <td className={`${styles.cell} ${styles.numeric}`}>
                {change(row.outcome.p_europe, currentEurope)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** The fixture, its stored forecasts, the opponent and the swing panel. */
async function NextMatchBody(
  props: NextMatchSectionProps,
): Promise<ReactElement> {
  const { teamId, season, teamName, upcoming } = props;
  const first: UpcomingMatch | undefined = upcoming[0];

  if (first === undefined) {
    return (
      <EmptyState title={`No fixture left in ${season}`}>
        <p>
          No further fixture is scheduled for {teamName} in this season, so
          there is no next match to preview.
        </p>
      </EmptyState>
    );
  }

  const match = first.match;
  const atHome = match.home_team.id === teamId;
  const opponent = atHome ? match.away_team : match.home_team;
  const kickoff =
    match.kickoff_time === null
      ? formatMadridDate(match.kickoff_date)
      : `${formatMadridDate(match.kickoff_date)}, ${formatMadridDateTime(match.kickoff_time)}`;

  const [statsResult, raceResult] = await Promise.all([
    loadSeasonStats(opponent.id, season),
    loadRace(teamId, season),
  ]);

  return (
    <>
      <dl className={styles.tiles}>
        <div className={styles.tile}>
          <dt className={styles.term}>Opponent</dt>
          <dd className={styles.value}>{opponent.name}</dd>
        </div>
        <div className={styles.tile}>
          <dt className={styles.term}>Kickoff</dt>
          <dd className={styles.value}>{kickoff}</dd>
        </div>
        <div className={styles.tile}>
          <dt className={styles.term}>Venue</dt>
          <dd className={styles.value}>{atHome ? "Home" : "Away"}</dd>
        </div>
      </dl>
      <p className={styles.note}>
        <Link href={`/matches/${match.id}`}>Match preview</Link>
      </p>

      {first.predictions.length === 0 ? (
        <p className={styles.note}>
          No model has stored a forecast for this fixture yet, so there is no
          probability bar to draw. The odds pages fill this once a prediction
          run stores one.
        </p>
      ) : (
        first.predictions.map((prediction) => (
          <ProbabilityBar
            id={`focus-next-${prediction.model}`}
            key={`${prediction.model}-${prediction.prediction_id}`}
            title={`${modelLabel(prediction.model)}: home win, draw and away win`}
            description={`The stored home win, draw and away win probabilities of the ${modelLabel(prediction.model)} model for this fixture, as of ${prediction.as_of_date}.`}
            home={{
              name: match.home_team.name,
              probability: prediction.probabilities.home,
            }}
            draw={{ probability: prediction.probabilities.draw }}
            away={{
              name: match.away_team.name,
              probability: prediction.probabilities.away,
            }}
            note={`Stored on ${formatMadridDate(prediction.created_at)}, as of ${formatMadridDate(prediction.as_of_date)}. A segment shows its share of the three stored probabilities.`}
          />
        ))
      )}

      {statsResult.ok ? (
        <OpponentProfile
          data={statsResult.data}
          opponentName={opponent.name}
        />
      ) : statsResult.status === 404 ? (
        <p className={styles.note}>
          {`The opponent profile is unavailable for the ${season} season, so only the fixture itself is shown.`}
        </p>
      ) : (
        <ProblemState problem={statsResult.problem} />
      )}

      <h3 className={styles.subheading}>Swing of the next match</h3>
      {raceResult.ok ? (
        <SwingTable race={raceResult.data} season={season} teamId={teamId} />
      ) : raceResult.status === 404 ? (
        <p className={styles.note}>
          The relegation race holds nothing for this club and season, so the
          swing of the next match is unknown.
        </p>
      ) : (
        <ProblemState problem={raceResult.problem} />
      )}
    </>
  );
}

/**
 * The streamed section: the heading is rendered on every path, and the body is
 * decided by the props and the two reads it makes itself.
 */
export async function NextMatchSection(
  props: NextMatchSectionProps,
): Promise<ReactElement> {
  return (
    <section className={styles.section} aria-labelledby="next-match-heading">
      <h2 className={styles.heading} id="next-match-heading">
        {SECTION_LABEL}
      </h2>
      <NextMatchBody {...props} />
    </section>
  );
}
