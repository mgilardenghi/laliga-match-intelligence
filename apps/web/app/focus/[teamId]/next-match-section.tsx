/**
 * "Next match": the club's next fixture, priced, and what each of its results
 * would do to the season.
 *
 * The section renders from four sources: the focus payload's `upcoming` list,
 * which the page already holds, the stored forecasts of that fixture, the
 * opponent's season statistics, and the relegation race, which carries the
 * swing of the next match together with its own baseline. Every swing number
 * comes from that one block, so the three outcomes and the "Now" row are read
 * against one run instead of against a second forecast.
 *
 * The swing belongs to a stored fixture, and the race names it. When the block
 * is absent, or when it describes a match other than the one listed here, the
 * section says which of those it is instead of printing a delta against the
 * wrong baseline.
 */
import Link from "next/link";
import type { ReactElement } from "react";

import { ProbabilityBar } from "@/components/charts";
import { EmptyState, ProblemState } from "@/components/states";
import { MetricHelp } from "@/components/ui";
import {
  EM_DASH,
  formatPercent,
  formatPoints,
  formatValue,
} from "@/lib/format";
import { modelLabel } from "@/lib/labels";
import { formatMadridDate, formatMadridDateTime } from "@/lib/madrid";

import {
  loadRace,
  loadSeasonStats,
  loadSimulationDetail,
  type RelegationRace,
  type TeamFocus,
  type TeamSeasonStats,
} from "./data";
import {
  changeInPoints,
  featuredField,
  qualificationLabels,
  storyBand,
  swingRows,
  swingSummary,
  type Qualification,
  type StoryBand,
} from "./story";
import styles from "./page.module.css";

/** The label the `<Suspense>` boundary of this section shows while it reads. */
export const SECTION_LABEL = "Next match";

/**
 * What the panel says when the stored run carries no swing for the fixture.
 *
 * The sentence names the three ways the block goes missing, because a reader
 * who sees no number is owed the reason rather than a placeholder.
 */
const SWING_MISSING =
  "No swing is stored for this match yet: the latest simulation predates swing support, its stored next match has since been played, or a result was never sampled. The next simulation run fills this in.";

/** What the panel says when the stored swing describes another fixture. */
const SWING_MISMATCH =
  "The stored swing describes a different fixture than the next one listed here, so it is not shown.";

/** The note that keeps two measurements of the same match from looking wrong. */
const TWO_MEASUREMENTS =
  "The bar is the stored forecast, the fixtures table the run's model probability, and 'Chance of result' how often the run's simulations produced it, so they can differ by about half a point.";

export type NextMatchSectionProps = {
  readonly teamId: number;
  /** The season the page resolved, never the raw query value. */
  readonly season: string;
  readonly teamName: string;
  /** The focus payload's next fixtures; the section reads the first one. */
  readonly upcoming: TeamFocus["upcoming"];
  /** The focus payload's outlook, which decides the metric the swing features. */
  readonly outlook: TeamFocus["outlook"];
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

/** The metric the swing features, named as a table column. */
function featuredLabel(
  band: StoryBand,
  qualification: Qualification | null,
): string {
  const labels = qualificationLabels(qualification);
  switch (featuredField(band)) {
    case "p_title":
      return "P(title)";
    case "p_europe":
      return `P(${labels.europe})`;
    case "p_relegation":
      return `P(${labels.relegation})`;
  }
}

/**
 * What each outcome of the next match would do to the club's season.
 *
 * The four rows are the run's own baseline and its three outcomes, and the
 * change column subtracts that baseline, so a reader compares all three results
 * on one scale. The featured probability follows the story card's band, and the
 * whole transposed block stays available for a reader who wants the other two.
 */
function SwingTable(props: {
  readonly race: RelegationRace;
  readonly band: StoryBand;
  readonly qualification: Qualification | null;
  readonly season: string;
}): ReactElement {
  const { race, band, qualification, season } = props;
  const swing = race.next_match;
  if (swing === null) {
    return <p className={styles.note}>{SWING_MISSING}</p>;
  }

  const rows = swingRows(swing, band);
  const summary = swingSummary(swing, qualification);

  return (
    <div className={styles.card}>
      <div className={styles.cardHead}>
        <h3 className={styles.cardTitle}>What this match changes</h3>
        <MetricHelp
          definition="The club's stored probabilities if the next match ends in a win, a draw or a loss, each read from the runs that produced that result, next to the same numbers for the season as it stands. The change column compares them with that baseline in percentage points, and 'Chance of result' is how often the run's simulations produced the outcome, so it can differ slightly from the model's own probability for the fixture."
          label="this match changes"
        />
      </div>
      <div className={styles.tableWrap}>
        <table className={styles.table}>
          <caption className={styles.caption}>
            {`Swing of the next match, ${season}`}
          </caption>
          <thead>
            <tr>
              {[
                "Outcome",
                "Chance of result",
                featuredLabel(band, qualification),
                "Change",
                "Expected points",
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
            {rows.map((row) => (
              <tr key={row.label}>
                <th className={`${styles.cell} ${styles.rowHeader}`} scope="row">
                  {row.label}
                </th>
                <td className={`${styles.cell} ${styles.numeric}`}>
                  {row.chance === null ? EM_DASH : formatPercent(row.chance)}
                </td>
                <td className={`${styles.cell} ${styles.numeric}`}>
                  {formatPercent(row.featured)}
                </td>
                <td className={`${styles.cell} ${styles.numeric}`}>
                  {row.change === null ? EM_DASH : changeInPoints(row.change)}
                </td>
                <td className={`${styles.cell} ${styles.numeric}`}>
                  {formatPoints(row.expectedPoints)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <details className={styles.disclosure}>
        <summary className={styles.summary}>All swing numbers</summary>
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <caption className={styles.caption}>
              {`Every swing number of the next match, ${season}`}
            </caption>
            <thead>
              <tr>
                {["Metric", "Now", "Win", "Draw", "Loss"].map((column, index) => (
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
              {summary.map((row) => (
                <tr key={row.label}>
                  <th className={`${styles.cell} ${styles.rowHeader}`} scope="row">
                    {row.label}
                  </th>
                  <td className={`${styles.cell} ${styles.numeric}`}>
                    {row.label === "Expected points"
                      ? formatPoints(row.now)
                      : formatPercent(row.now)}
                  </td>
                  <td className={`${styles.cell} ${styles.numeric}`}>
                    {row.label === "Expected points"
                      ? formatPoints(row.win)
                      : formatPercent(row.win)}
                  </td>
                  <td className={`${styles.cell} ${styles.numeric}`}>
                    {row.label === "Expected points"
                      ? formatPoints(row.draw)
                      : formatPercent(row.draw)}
                  </td>
                  <td className={`${styles.cell} ${styles.numeric}`}>
                    {row.label === "Expected points"
                      ? formatPoints(row.loss)
                      : formatPercent(row.loss)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
      <p className={styles.cardNote}>{TWO_MEASUREMENTS}</p>
    </div>
  );
}

/** The fixture, its stored forecasts, the swing panel and the opponent. */
async function NextMatchBody(
  props: NextMatchSectionProps,
): Promise<ReactElement> {
  const { teamId, season, teamName, upcoming, outlook } = props;
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

  // The place counts come from the run the race names, which is the run the
  // swing comes from; a failed read leaves the labels generic instead of
  // guessing the league's rules.
  const simulationResult =
    raceResult.ok && raceResult.data.run_id !== null
      ? await loadSimulationDetail(raceResult.data.run_id)
      : null;
  const qualification =
    simulationResult !== null && simulationResult.ok
      ? simulationResult.data.run.qualification
      : null;
  const band = storyBand(outlook, qualification);

  const swing = raceResult.ok ? raceResult.data.next_match : null;

  const firstPrediction = first.predictions[0];
  const otherPredictions = first.predictions.slice(1);

  return (
    <>
      <h3 className={styles.fixtureTitle}>
        {`${match.home_team.name} – ${match.away_team.name}`}
      </h3>
      <p className={styles.fixtureMeta}>
        {`${kickoff} · ${atHome ? "Home" : "Away"} · `}
        <Link href={`/matches/${match.id}`}>Full match preview</Link>
      </p>

      {firstPrediction === undefined ? (
        <p className={styles.note}>
          No model has stored a forecast for this fixture yet, so there is no
          probability bar to draw. The odds pages fill this once a prediction
          run stores one.
        </p>
      ) : (
        <>
          <ProbabilityBar
            id={`focus-next-${firstPrediction.model}`}
            key={`${firstPrediction.model}-${firstPrediction.prediction_id}`}
            title={`${modelLabel(firstPrediction.model)}: home win, draw and away win`}
            description={`The stored home win, draw and away win probabilities of the ${modelLabel(firstPrediction.model)} model for this fixture, as of ${firstPrediction.as_of_date}.`}
            home={{
              name: match.home_team.name,
              probability: firstPrediction.probabilities.home,
            }}
            draw={{ probability: firstPrediction.probabilities.draw }}
            away={{
              name: match.away_team.name,
              probability: firstPrediction.probabilities.away,
            }}
            note={`Stored on ${formatMadridDate(firstPrediction.created_at)}, as of ${formatMadridDate(firstPrediction.as_of_date)}. A segment shows its share of the three stored probabilities.`}
          />
          {otherPredictions.length === 0 ? null : (
            <details className={styles.disclosure}>
              <summary className={styles.summary}>Other stored forecasts</summary>
              {otherPredictions.map((prediction) => (
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
              ))}
            </details>
          )}
        </>
      )}

      {raceResult.ok ? (
        swing === null ? (
          <p className={styles.note}>{SWING_MISSING}</p>
        ) : swing.match_id !== match.id ? (
          <p className={styles.note}>{SWING_MISMATCH}</p>
        ) : (
          <SwingTable
            band={band}
            qualification={qualification}
            race={raceResult.data}
            season={season}
          />
        )
      ) : raceResult.status === 404 ? (
        <p className={styles.note}>
          The relegation race holds nothing for this club and season, so the
          swing of the next match is unknown.
        </p>
      ) : (
        <ProblemState problem={raceResult.problem} />
      )}

      {statsResult.ok ? (
        <details className={styles.disclosure}>
          <summary className={styles.summary}>
            {`${opponent.name} per match`}
          </summary>
          <OpponentProfile data={statsResult.data} opponentName={opponent.name} />
        </details>
      ) : statsResult.status === 404 ? (
        <p className={styles.note}>
          {`The opponent profile is unavailable for the ${season} season, so only the fixture itself is shown.`}
        </p>
      ) : (
        <ProblemState problem={statsResult.problem} />
      )}
    </>
  );
}

/**
 * The streamed section: the heading is rendered on every path, and the body is
 * decided by the props and the reads it makes itself.
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
