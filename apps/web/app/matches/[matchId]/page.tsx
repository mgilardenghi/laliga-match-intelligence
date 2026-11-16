/**
 * The match page: one fixture, and what a reader could have known before its
 * kickoff.
 *
 * The page is a preview first. Its probabilities, its two form summaries and its
 * head-to-head record are the stored, pre-kickoff view of the fixture, so a
 * match that has since been played still shows what a reader could have seen
 * before the whistle, next to the result it produced. That is why the
 * probability section is headed `Pre-match view` rather than carrying a claim
 * about now. A row the worker stamped at or after matchday midnight is the one
 * exception: it is named in a sentence of its own rather than drawn, so nothing
 * under that heading claims to predate the kickoff without earning it.
 *
 * Four reads serve it: the fixture (the only source of a played match's
 * statistics), the preview (both form summaries, the record and the stored
 * forecasts), and one season-statistics read per club for the profile against
 * the league. The two profile reads are the only optional ones: a club without
 * season statistics loses its profile, never the page.
 *
 * Out of scope on purpose: the design's per-fixture probabilities and the
 * relegation swing a result causes are not M5.
 */
import Link from "next/link";
import { notFound } from "next/navigation";
import type { ReactElement } from "react";

import { FormStrip, ProbabilityBar } from "@/components/charts";
import { EmptyState, ProblemState } from "@/components/states";
import { get, type ApiResult, type GetData, type Problem } from "@/lib/api";
import { EM_DASH, formatCount, formatValue } from "@/lib/format";
import { matchStatusLabel, modelLabel, outcomeLabel } from "@/lib/labels";
import { formatMadridDate, formatMadridDateTime } from "@/lib/madrid";

import { MirroredBars, type MirroredRow } from "./MirroredBars";
import styles from "./page.module.css";

export const dynamic = "force-dynamic";

type MatchIdProps = {
  readonly params: Promise<{ matchId: string }>;
};

type MatchSummary = GetData<"/v1/matches/{match_id}/preview">["match"];
type Preview = GetData<"/v1/matches/{match_id}/preview">;
type MatchDetail = GetData<"/v1/matches/{match_id}">;
type SeasonStats = GetData<"/v1/teams/{team_id}/season-stats">;
type StatLine = SeasonStats["per_match"]["against"];

/** The evaluation the API gives a row stamped at or after matchday midnight. */
const LATE_EVALUATION = "late";

type Prediction = Preview["predictions"][number];

/**
 * Name the stored rows that are not pre-kickoff forecasts.
 *
 * The API marks a row `late` when the worker stamped it at or after matchday
 * midnight. Such a row is not a forecast anyone could have read before the
 * whistle, so it is stated here instead of being drawn next to the pre-kickoff
 * bars, where it would borrow their claim. The evaluation is derived by the API
 * when it reads the row, so the sentence says the stored prediction stays and the
 * API labels it late, not that `late` is a stored column.
 */
function lateNote(late: readonly Prediction[]): string {
  const models = late.map((prediction) => modelLabel(prediction.model)).join(", ");
  return late.length === 1
    ? `${models} was stamped at or after matchday midnight for this fixture, so it is not a pre-kickoff forecast and is not drawn here. The stored prediction remains, and the API labels it late.`
    : `${models} were stamped at or after matchday midnight for this fixture, so they are not pre-kickoff forecasts and are not drawn here. The stored predictions remain, and the API labels them late.`;
}

/** The metrics of the profile, in the order the design lists them. */
const PROFILE_METRICS: readonly { readonly label: string; readonly key: keyof StatLine }[] = [
  { label: "Goals", key: "goals" },
  { label: "Shots", key: "shots" },
  { label: "Shots on target", key: "shots_on_target" },
  { label: "Expected goals", key: "xg" },
  { label: "Fouls", key: "fouls" },
  { label: "Yellow cards", key: "yellow" },
  { label: "Red cards", key: "red" },
];

/**
 * Return the fixture identifier, or `null` for anything that is not one.
 *
 * A non-numeric or non-positive segment names no resource, so the page answers
 * `notFound()` instead of asking the API for a `422` it cannot render.
 */
function parseMatchId(value: string): number | null {
  if (!/^\d+$/.test(value)) {
    return null;
  }
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : null;
}

/** Return `home–away`, with an em dash for a goal that is not known. */
function scoreLine(fixture: MatchSummary): string {
  return `${formatCount(fixture.home_goals)}–${formatCount(fixture.away_goals)}`;
}

/** The page a handled refusal renders, with the heading still in place. */
function RefusedPage(props: { readonly problem: Problem | null }): ReactElement {
  return (
    <div className={styles.main}>
      <header className={styles.header}>
        <h1 className={styles.title}>Match preview</h1>
      </header>
      <ProblemState problem={props.problem} />
    </div>
  );
}

/**
 * Build the profile rows of one side.
 *
 * Expected goals is left out unless the season is covered, because a row of
 * em dashes would suggest the clubs averaged nothing rather than that the source
 * published no expected goals.
 */
function profileRows(stats: SeasonStats): readonly MirroredRow[] {
  const covered = stats.xg_coverage > 0;
  return PROFILE_METRICS.filter(
    (metric) => metric.key !== "xg" || covered,
  ).map((metric) => ({
    label: metric.label,
    own: stats.per_match.for[metric.key],
    against: stats.per_match.against[metric.key],
    league: stats.league_avg[metric.key],
  }));
}

/** One club's profile: the chart, or the honest reason it is missing. */
function ProfileSection(props: {
  readonly name: string;
  readonly season: string;
  readonly stats: ApiResult<SeasonStats>;
}): ReactElement {
  const { name, season, stats } = props;
  if (!stats.ok) {
    return (
      <EmptyState title={`No season statistics for ${name}`}>
        <p>
          The API holds no per-match statistics for {name} in {season}, so there
          is no profile to compare with the league average.
        </p>
      </EmptyState>
    );
  }
  const covered = stats.data.xg_coverage > 0;
  return (
    <MirroredBars
      id={`profile-${stats.data.team.id}`}
      title={`${name} per-match profile`}
      description={`Per-match averages of ${name} and of the clubs it faced, each against the league average of the ${season} season. Every row is scaled to its own largest value and the tick marks the league average.`}
      ownLabel={name}
      againstLabel="Opponents"
      rows={profileRows(stats.data)}
      note={
        covered
          ? "Each row has its own scale, because the metrics count different things. The tick on a row is the league average."
          : `Each row has its own scale, because the metrics count different things. Expected goals is left out: the source published none for any of the ${formatCount(stats.data.played)} played matches of this season.`
      }
    />
  );
}

export default async function MatchPage(
  props: MatchIdProps,
): Promise<ReactElement> {
  const { matchId } = await props.params;
  const id = parseMatchId(matchId);
  if (id === null) {
    notFound();
  }

  const [matchResult, previewResult] = await Promise.all([
    get("/v1/matches/{match_id}", { path: { match_id: id } }),
    get("/v1/matches/{match_id}/preview", { path: { match_id: id } }),
  ]);

  if (!matchResult.ok) {
    if (matchResult.status === 404) {
      notFound();
    }
    return <RefusedPage problem={matchResult.problem} />;
  }
  if (!previewResult.ok) {
    if (previewResult.status === 404) {
      notFound();
    }
    return <RefusedPage problem={previewResult.problem} />;
  }

  const fixture: MatchDetail = matchResult.data;
  const preview: Preview = previewResult.data;
  const season = preview.match.season;

  // A row the API marks `late` was stamped at or after matchday midnight, so it
  // is not part of the pre-kickoff view this page claims to show.
  const late = preview.predictions.filter(
    (prediction) => prediction.evaluation === LATE_EVALUATION,
  );
  const preMatch = preview.predictions.filter(
    (prediction) => prediction.evaluation !== LATE_EVALUATION,
  );

  // The profile is a per-season view of each club, so both reads pass the
  // fixture's season. Neither can fail the page: the section that uses them
  // reports its own gap.
  const [homeStats, awayStats] = await Promise.all([
    get("/v1/teams/{team_id}/season-stats", {
      path: { team_id: fixture.home_team.id },
      query: { season },
    }),
    get("/v1/teams/{team_id}/season-stats", {
      path: { team_id: fixture.away_team.id },
      query: { season },
    }),
  ]);

  const home = fixture.home_team.name;
  const away = fixture.away_team.name;
  const played = fixture.status === "played";
  const record = preview.head_to_head;
  const odds = preview.odds;

  return (
    <div className={styles.main}>
      <header className={styles.header}>
        <h1 className={styles.title}>{`${home} vs ${away}`}</h1>
        <p className={styles.meta}>
          <span>{formatMadridDate(fixture.kickoff_date)}</span>
          <span>{`${formatMadridDateTime(fixture.kickoff_time)} source local time`}</span>
          <span>{matchStatusLabel(fixture.status)}</span>
          {fixture.matchday === null ? null : (
            <Link href={`/matchday?matchday=${fixture.matchday}`}>
              {`Matchday ${fixture.matchday}`}
            </Link>
          )}
        </p>
        {played ? (
          <p className={styles.result}>
            <span className={styles.score}>{scoreLine(fixture)}</span>
            <span>
              {fixture.result === null ? EM_DASH : outcomeLabel(fixture.result)}
            </span>
          </p>
        ) : null}
      </header>

      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>Pre-match view</h2>
        <p className={styles.lead}>
          Every probability and record below is the stored pre-kickoff view of
          this fixture, not a forecast made after the result.
        </p>
        {preview.predictions.length === 0 ? (
          <EmptyState
            title="No predictions stored for this match"
            command="make predict"
          >
            <p>
              No model has a stored forecast for this fixture, so the page has no
              stored probability to show. The market price below, when there is
              one, is the bookmaker&apos;s, not a model&apos;s.
            </p>
          </EmptyState>
        ) : null}
        {late.length === 0 ? null : (
          <p className={styles.note}>{lateNote(late)}</p>
        )}
        {preMatch.length === 0 && odds === null ? null : (
          <div className={styles.charts}>
            {preMatch.map((prediction) => (
              <ProbabilityBar
                key={prediction.prediction_id}
                id={`match-${fixture.id}-${prediction.model}`}
                title={`${modelLabel(prediction.model)} probabilities`}
                home={{
                  name: home,
                  probability: prediction.probabilities.home,
                }}
                draw={{ probability: prediction.probabilities.draw }}
                away={{
                  name: away,
                  probability: prediction.probabilities.away,
                }}
                note={`Pre-kickoff forecast, evaluation: ${prediction.evaluation}.`}
              />
            ))}
            {odds === null ? null : (
              <ProbabilityBar
                id={`match-${fixture.id}-market-implied`}
                title="Market implied probabilities"
                home={{ name: home, probability: odds.implied.home }}
                draw={{ probability: odds.implied.draw }}
                away={{ name: away, probability: odds.implied.away }}
                note={`Pre-closing odds, margin removed, from ${odds.bookmaker}.`}
              />
            )}
          </div>
        )}
      </section>

      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>Form</h2>
        <p className={styles.lead}>
          The last played matches of each club dated strictly before this
          fixture, newest first.
        </p>
        <div className={styles.charts}>
          {[
            { key: "home", name: home, form: preview.home_form },
            { key: "away", name: away, form: preview.away_form },
          ].map((side) => (
            <div className={styles.formBlock} key={side.key}>
              <h3 className={styles.subTitle}>{`${side.name} — recent form`}</h3>
              <FormStrip id={`match-${fixture.id}-${side.key}-form`} results={side.form.results} />
              <p className={styles.note}>
                {`${formatCount(side.form.points)} points, ${formatCount(side.form.goals_for)} goals for and ${formatCount(side.form.goals_against)} against in the ${formatCount(side.form.matches.length)} matches before kickoff.`}
              </p>
            </div>
          ))}
        </div>
      </section>

      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>Head-to-head</h2>
        {record.played === 0 ? (
          <EmptyState title="No previous meetings">
            <p>
              The two clubs have not met before this fixture, so there is no
              record to compare.
            </p>
          </EmptyState>
        ) : (
          <>
            <div className={styles.tableWrap}>
              <table className={styles.table}>
                <caption className={styles.caption}>
                  {`The ${record.played} meetings of ${home} and ${away} before this fixture, counted from this fixture's home side`}
                </caption>
                <thead>
                  <tr>
                    <th className={`${styles.cell} ${styles.headCell} ${styles.stickyColumn}`} scope="col">
                      Record
                    </th>
                    <th className={`${styles.cell} ${styles.headCell}`} scope="col">
                      {`${home} wins`}
                    </th>
                    <th className={`${styles.cell} ${styles.headCell}`} scope="col">
                      Draws
                    </th>
                    <th className={`${styles.cell} ${styles.headCell}`} scope="col">
                      {`${away} wins`}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <th
                      className={`${styles.cell} ${styles.stickyColumn}`}
                      scope="row"
                    >
                      Before kickoff
                    </th>
                    <td className={styles.cell}>{formatCount(record.home_side_wins)}</td>
                    <td className={styles.cell}>{formatCount(record.draws)}</td>
                    <td className={styles.cell}>{formatCount(record.away_side_wins)}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div className={styles.tableWrap}>
              <table className={styles.table}>
                <caption className={styles.caption}>
                  Last 10 meetings, newest first
                </caption>
                <thead>
                  <tr>
                    <th className={`${styles.cell} ${styles.headCell} ${styles.stickyColumn}`} scope="col">
                      Date
                    </th>
                    <th className={`${styles.cell} ${styles.headCell}`} scope="col">
                      Home
                    </th>
                    <th className={`${styles.cell} ${styles.headCell}`} scope="col">
                      Score
                    </th>
                    <th className={`${styles.cell} ${styles.headCell}`} scope="col">
                      Away
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {record.matches.map((meeting) => (
                    <tr key={meeting.id}>
                      <th
                        className={`${styles.cell} ${styles.stickyColumn} ${styles.nowrap}`}
                        scope="row"
                      >
                        <Link href={`/matches/${meeting.id}`}>
                          {formatMadridDate(meeting.kickoff_date)}
                        </Link>
                      </th>
                      <td className={styles.cell}>{meeting.home_team.name}</td>
                      <td className={`${styles.cell} ${styles.nowrap}`}>
                        {scoreLine(meeting)}
                      </td>
                      <td className={styles.cell}>{meeting.away_team.name}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </section>

      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>Opponent profile</h2>
        <p className={styles.lead}>
          What each side does per match over the {season} season, what its
          opponents did to it, and where the league average sits.
        </p>
        <div className={styles.charts}>
          <ProfileSection name={home} season={season} stats={homeStats} />
          <ProfileSection name={away} season={season} stats={awayStats} />
        </div>
      </section>

      {played ? (
        <section className={styles.section}>
          <h2 className={styles.sectionTitle}>Result</h2>
          <p className={styles.result}>
            <span className={styles.score}>{scoreLine(fixture)}</span>
            <span>
              {fixture.result === null ? EM_DASH : outcomeLabel(fixture.result)}
            </span>
          </p>
          <p className={styles.note}>
            {`Half-time: ${formatCount(fixture.ht_home_goals)}–${formatCount(fixture.ht_away_goals)}`}
          </p>
          {fixture.stats === null ? (
            <EmptyState title="No statistics row for this match">
              <p>
                The source published no statistics for this fixture, so there are
                no shots, fouls or cards to show. The result above is stored.
              </p>
            </EmptyState>
          ) : (
            <div className={styles.tableWrap}>
              <table className={styles.table}>
                <caption className={styles.caption}>
                  {`Match statistics of ${home} and ${away}`}
                </caption>
                <thead>
                  <tr>
                    <th className={`${styles.cell} ${styles.headCell} ${styles.stickyColumn}`} scope="col">
                      Metric
                    </th>
                    <th className={`${styles.cell} ${styles.headCell}`} scope="col">
                      {home}
                    </th>
                    <th className={`${styles.cell} ${styles.headCell}`} scope="col">
                      {away}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {[
                    {
                      label: "Shots",
                      home: formatCount(fixture.stats.home_shots),
                      away: formatCount(fixture.stats.away_shots),
                    },
                    {
                      label: "Shots on target",
                      home: formatCount(fixture.stats.home_shots_on_target),
                      away: formatCount(fixture.stats.away_shots_on_target),
                    },
                    {
                      label: "Fouls",
                      home: formatCount(fixture.stats.home_fouls),
                      away: formatCount(fixture.stats.away_fouls),
                    },
                    {
                      label: "Corners",
                      home: formatCount(fixture.stats.home_corners),
                      away: formatCount(fixture.stats.away_corners),
                    },
                    {
                      label: "Yellow cards",
                      home: formatCount(fixture.stats.home_yellow),
                      away: formatCount(fixture.stats.away_yellow),
                    },
                    {
                      label: "Red cards",
                      home: formatCount(fixture.stats.home_red),
                      away: formatCount(fixture.stats.away_red),
                    },
                    {
                      label: "Expected goals",
                      home: formatValue(fixture.stats.home_xg, 2),
                      away: formatValue(fixture.stats.away_xg, 2),
                    },
                  ].map((row) => (
                    <tr key={row.label}>
                      <th
                        className={`${styles.cell} ${styles.stickyColumn}`}
                        scope="row"
                      >
                        {row.label}
                      </th>
                      <td className={styles.cell}>{row.home}</td>
                      <td className={styles.cell}>{row.away}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      ) : null}
    </div>
  );
}
