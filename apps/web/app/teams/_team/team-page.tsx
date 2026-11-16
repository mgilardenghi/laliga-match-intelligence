/**
 * The team page: one club's season, read from the API and rendered on the server.
 *
 * The page answers the questions a reader has about a single club: where it
 * stands, how it earned those points home and away, how its per-match averages
 * compare with the league, which results produced them and what comes next.
 * Every number is read from the API; the page recomputes nothing, so a
 * correction to a rule of the core package reaches it without a change here.
 *
 * The module owns no route configuration and no path parsing. The route file
 * that imports it declares `dynamic = "force-dynamic"`, validates the raw
 * `teamId` segment and awaits the search parameters, so this component receives
 * a positive safe integer and plain values. The one `notFound()` call left is
 * the API's answer that no club has this identifier, which is the page that
 * does not exist rather than a state of the data.
 */
import Link from "next/link";
import { notFound } from "next/navigation";
import type { ReactElement } from "react";

import { ChartFigure, FormStrip, ProbabilityBar } from "@/components/charts";
import { SeasonSelect, type SeasonOption } from "@/components/shell";
import { EmptyState, ProblemState } from "@/components/states";
import { get, type GetData } from "@/lib/api";
import { setFocusTeam } from "@/lib/focus-actions";
import {
  EM_DASH,
  formatCount,
  formatPercent,
  formatPoints,
  formatSigned,
  formatValue,
} from "@/lib/format";
import { formatMadridDate, formatMadridDateTime } from "@/lib/madrid";
import { parseSeason, type SearchParams } from "@/lib/params";

import { PROFILE_METRICS, ProfileBars } from "./profile-bars";
import styles from "./team-page.module.css";

export type TeamPageProps = {
  readonly teamId: number;
  readonly params: SearchParams;
};

type SeasonsPayload = GetData<"/v1/seasons">;
type FocusPayload = GetData<"/v1/teams/{team_id}/focus">;
type SplitRecord = FocusPayload["splits"]["home"];
type UpcomingFixture = FocusPayload["upcoming"][number];

/**
 * The seasons the selector offers, newest first.
 *
 * Later seasons than the one the page resolved are left out: they are stored but
 * not played, and offering them would take the reader to a season the page
 * cannot describe. Without an anchor — the URL named no season and nothing was
 * resolved yet — every season is offered rather than none.
 */
function seasonOptions(
  seasons: SeasonsPayload,
  anchor: string | null,
): SeasonOption[] {
  // Copied rather than sorted in place: the API's order is not this page's to
  // reorder.
  const ordered = [...seasons].sort((a, b) => b.start_year - a.start_year);
  const anchorStartYear =
    anchor === null
      ? null
      : (ordered.find((entry) => entry.code === anchor)?.start_year ?? null);
  const listed =
    anchorStartYear === null
      ? ordered
      : ordered.filter((entry) => entry.start_year <= anchorStartYear);
  return listed.map((entry) => ({
    code: entry.code,
    startYear: entry.start_year,
  }));
}

/**
 * The heading of the page, or nothing when the club name could not be read.
 *
 * It is a plain block and not a `<header>` element: the shell owns the document
 * header, and a second one would read as a duplicate landmark. The season line
 * appears only once a season is resolved, because it names the season the page
 * below it describes.
 */
function PageHeading(props: {
  readonly name: string | null;
  readonly season: string | null;
}): ReactElement | null {
  const { name, season } = props;
  if (name === null) {
    return null;
  }
  return (
    <div className={styles.heading}>
      <h1 className={styles.title}>{name}</h1>
      {season === null ? null : (
        <p className={styles.season}>Season {season}</p>
      )}
    </div>
  );
}

/** The season selector, kept at the width of its own control. */
function SeasonPicker(props: {
  readonly options: readonly SeasonOption[];
  readonly value: string | null;
  readonly path: string;
}): ReactElement | null {
  const { options, value, path } = props;
  if (options.length === 0) {
    return null;
  }
  return (
    <div className={styles.controls}>
      <SeasonSelect options={options} value={value} path={path} />
    </div>
  );
}

export async function TeamPage(props: TeamPageProps): Promise<ReactElement> {
  const { teamId, params } = props;
  const path = `/teams/${teamId}`;
  const requestedSeason = parseSeason(params.season);

  const [seasonsResult, teamResult] = await Promise.all([
    get("/v1/seasons"),
    get("/v1/teams/{team_id}", { path: { team_id: teamId } }),
  ]);

  // The selector is built from the seasons list, so a rejected read leaves the
  // page with nothing to offer and is reported instead of drawn.
  if (!seasonsResult.ok) {
    return (
      <div className={styles.page}>
        <PageHeading
          name={teamResult.ok ? teamResult.data.name : null}
          season={null}
        />
        <ProblemState problem={seasonsResult.problem} />
      </div>
    );
  }

  // No club has this identifier, which is the route's own "not found"; any
  // other refusal is the API answering about a club it does have.
  if (!teamResult.ok) {
    if (teamResult.status === 404) {
      notFound();
    }
    return (
      <div className={styles.page}>
        <PageHeading name={null} season={null} />
        <ProblemState problem={teamResult.problem} />
      </div>
    );
  }
  const team = teamResult.data;

  // Without a season in the URL the API answers with the season that starts
  // latest among the seasons with a match, and that answer is the season every
  // read below asks for. With one, the API accepts it or refuses it.
  const focusResult =
    requestedSeason === null
      ? await get("/v1/teams/{team_id}/focus", {
          path: { team_id: teamId },
        })
      : await get("/v1/teams/{team_id}/focus", {
          path: { team_id: teamId },
          query: { season: requestedSeason },
        });

  if (!focusResult.ok) {
    const options = seasonOptions(seasonsResult.data, requestedSeason);
    const picker = (
      <SeasonPicker options={options} value={requestedSeason} path={path} />
    );
    // A 404 here is not an unknown club: it is a season the club does not play
    // or a code the API does not know, so the reader keeps the page and moves
    // to another season instead of landing on the route's not-found screen.
    if (focusResult.status === 404) {
      return (
        <div className={styles.page}>
          <PageHeading name={team.name} season={requestedSeason} />
          {picker}
          <EmptyState title="No team record for this season">
            <p>
              The API has this club but no record of it in the requested
              season. That season may not be one the club played, or it may not
              have been loaded yet. Pick another season above.
            </p>
          </EmptyState>
        </div>
      );
    }
    return (
      <div className={styles.page}>
        <PageHeading name={team.name} season={requestedSeason} />
        {picker}
        <ProblemState problem={focusResult.problem} />
      </div>
    );
  }

  const focus = focusResult.data;
  const season = focus.season;
  const standing = focus.standing;
  const outlook = focus.outlook;
  const options = seasonOptions(seasonsResult.data, season);
  const redirectTo = `${path}?season=${encodeURIComponent(season)}`;

  const statsResult = await get("/v1/teams/{team_id}/season-stats", {
    path: { team_id: teamId },
    query: { season },
  });

  const controls = (
    <div className={styles.controls}>
      <SeasonSelect options={options} value={season} path={path} />
      <form className={styles.focusForm} action={setFocusTeam}>
        <input type="hidden" name="teamId" value={teamId} />
        <input type="hidden" name="redirectTo" value={redirectTo} />
        <button className={styles.button} type="submit">
          Make this my focus team
        </button>
      </form>
      <Link
        className={styles.focusLink}
        href={`/focus/${teamId}?season=${encodeURIComponent(season)}`}
      >
        Open focus view
      </Link>
    </div>
  );

  // The statistics read feeds three of the five sections, so a refusal is
  // reported once for the page instead of three times for the sections.
  if (!statsResult.ok) {
    return (
      <div className={styles.page}>
        <PageHeading name={team.name} season={season} />
        {controls}
        <ProblemState problem={statsResult.problem} />
      </div>
    );
  }
  const stats = statsResult.data;

  const venueRows: readonly {
    readonly venue: string;
    readonly record: SplitRecord;
  }[] = [
    { venue: "Home", record: focus.splits.home },
    { venue: "Away", record: focus.splits.away },
  ];

  // The API serves the played matches chronologically, and a results list is
  // read backwards: the last match played comes first.
  const playedMatches = [...stats.matches].reverse();

  const profileTable = {
    caption: `Per-match averages against the league average, ${season}`,
    columns: ["Metric", "For", "Against", "League average"],
    rows: PROFILE_METRICS.map((metric) => [
      metric.label,
      formatValue(stats.per_match.for[metric.key]),
      formatValue(stats.per_match.against[metric.key]),
      formatValue(stats.league_avg[metric.key]),
    ]),
  };

  return (
    <div className={styles.page}>
      <PageHeading name={team.name} season={season} />
      {controls}

      <section aria-labelledby="season-at-a-glance" className={styles.section}>
        <h2 id="season-at-a-glance" className={styles.sectionTitle}>
          Season at a glance
        </h2>
        <dl className={styles.glance}>
          <div className={styles.glanceRow}>
            <dt className={styles.glanceTerm}>Position</dt>
            <dd className={styles.glanceValue}>
              {standing === null ? EM_DASH : formatCount(standing.position)}
            </dd>
          </div>
          <div className={styles.glanceRow}>
            <dt className={styles.glanceTerm}>Points</dt>
            <dd className={styles.glanceValue}>
              {standing === null ? EM_DASH : formatCount(standing.points)}
            </dd>
          </div>
          <div className={styles.glanceRow}>
            <dt className={styles.glanceTerm}>Market xPts</dt>
            <dd className={styles.glanceValue}>
              {stats.xpts_market === null ? EM_DASH : formatPoints(stats.xpts_market)}{" "}
              {`(${formatCount(stats.xpts_coverage)} of ${formatCount(
                stats.played,
              )} matches priced)`}
            </dd>
          </div>
          <div className={styles.glanceRow}>
            <dt className={styles.glanceTerm}>Goal difference</dt>
            <dd className={styles.glanceValue}>
              {standing === null ? EM_DASH : formatSigned(standing.goal_difference)}
            </dd>
          </div>
          <div className={styles.glanceRow}>
            <dt className={styles.glanceTerm}>P(relegation)</dt>
            <dd className={styles.glanceValue}>
              {outlook === null ? EM_DASH : formatPercent(outlook.p_relegation)}
            </dd>
          </div>
        </dl>
        <p className={styles.note}>
          Relegation risk is the stored probability of the latest succeeded
          simulation. A season without one shows a dash: run{" "}
          <code className={styles.code}>make simulate</code> to add it. Market
          xPts sums the expected points of the pre-closing prices the season
          carries, and the count in brackets names how many matches that was.
        </p>
      </section>

      <section aria-labelledby="home-and-away" className={styles.section}>
        <h2 id="home-and-away" className={styles.sectionTitle}>
          Home and away
        </h2>
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <caption className={styles.caption}>
              Home and away record, {season}
            </caption>
            <thead>
              <tr>
                <th
                  className={`${styles.cell} ${styles.headCell} ${styles.stickyColumn}`}
                  scope="col"
                >
                  Venue
                </th>
                <th className={`${styles.cell} ${styles.headCell} ${styles.numeric}`} scope="col">
                  P
                </th>
                <th className={`${styles.cell} ${styles.headCell} ${styles.numeric}`} scope="col">
                  W
                </th>
                <th className={`${styles.cell} ${styles.headCell} ${styles.numeric}`} scope="col">
                  D
                </th>
                <th className={`${styles.cell} ${styles.headCell} ${styles.numeric}`} scope="col">
                  L
                </th>
                <th className={`${styles.cell} ${styles.headCell} ${styles.numeric}`} scope="col">
                  GF
                </th>
                <th className={`${styles.cell} ${styles.headCell} ${styles.numeric}`} scope="col">
                  GA
                </th>
                <th className={`${styles.cell} ${styles.headCell} ${styles.numeric}`} scope="col">
                  Pts
                </th>
                <th className={`${styles.cell} ${styles.headCell} ${styles.numeric}`} scope="col">
                  PPG
                </th>
              </tr>
            </thead>
            <tbody>
              {venueRows.map((row) => (
                <tr key={row.venue}>
                  <th
                    className={`${styles.cell} ${styles.stickyColumn} ${styles.venueCell}`}
                    scope="row"
                  >
                    {row.venue}
                  </th>
                  <td className={`${styles.cell} ${styles.numeric}`}>
                    {formatCount(row.record.played)}
                  </td>
                  <td className={`${styles.cell} ${styles.numeric}`}>
                    {formatCount(row.record.won)}
                  </td>
                  <td className={`${styles.cell} ${styles.numeric}`}>
                    {formatCount(row.record.drawn)}
                  </td>
                  <td className={`${styles.cell} ${styles.numeric}`}>
                    {formatCount(row.record.lost)}
                  </td>
                  <td className={`${styles.cell} ${styles.numeric}`}>
                    {formatCount(row.record.goals_for)}
                  </td>
                  <td className={`${styles.cell} ${styles.numeric}`}>
                    {formatCount(row.record.goals_against)}
                  </td>
                  <td className={`${styles.cell} ${styles.numeric}`}>
                    {formatCount(row.record.points)}
                  </td>
                  <td className={`${styles.cell} ${styles.numeric}`}>
                    {formatPoints(row.record.points_per_game)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section aria-labelledby="for-and-against" className={styles.section}>
        <h2 id="for-and-against" className={styles.sectionTitle}>
          For and against
        </h2>
        {stats.stat_coverage === 0 ? (
          <EmptyState
            title="No match statistics stored for this season"
            command="make ingest"
          >
            <p>
              The fixtures of this season are stored, but none of them carries a
              statistics row, so there is no per-match average to draw.
            </p>
          </EmptyState>
        ) : (
          <ChartFigure
            title="Per-match averages against the league average"
            description="Mirrored bars: the club's per-match average extends to the right, the average of the clubs it faced to the left, and the tick on both sides marks the league average."
            table={profileTable}
            note={`Each row has its own scale, because the metrics count different things. The tick on a row is the league average. Averages cover ${formatCount(
              stats.stat_coverage,
            )} of ${formatCount(stats.played)} matches; expected goals cover ${formatCount(
              stats.xg_coverage,
            )} of ${formatCount(stats.played)}.`}
          >
            <ProfileBars
              forLine={stats.per_match.for}
              againstLine={stats.per_match.against}
              leagueLine={stats.league_avg}
              id={`profile-${teamId}`}
            />
          </ChartFigure>
        )}
      </section>

      <section aria-labelledby="results" className={styles.section}>
        <h2 id="results" className={styles.sectionTitle}>
          Results
        </h2>
        {focus.form.results.length > 0 ? (
          <FormStrip results={focus.form.results} id={`form-${teamId}`} />
        ) : null}
        {stats.played === 0 || playedMatches.length === 0 ? (
          <EmptyState title={`No results in ${season} yet`} command="make ingest">
            <p>
              The season is stored, but no match of this club has been played
              yet, so there is no result to list.
            </p>
          </EmptyState>
        ) : (
          <div className={styles.tableWrap}>
            <table className={styles.table}>
              <caption className={styles.caption}>Results, {season}</caption>
              <thead>
                <tr>
                  <th
                    className={`${styles.cell} ${styles.headCell} ${styles.stickyColumn}`}
                    scope="col"
                  >
                    Date
                  </th>
                  <th className={`${styles.cell} ${styles.headCell} ${styles.numeric}`} scope="col">
                    MD
                  </th>
                  <th className={`${styles.cell} ${styles.headCell}`} scope="col">
                    Venue
                  </th>
                  <th className={`${styles.cell} ${styles.headCell}`} scope="col">
                    Opponent
                  </th>
                  <th className={`${styles.cell} ${styles.headCell} ${styles.numeric}`} scope="col">
                    Score
                  </th>
                  <th className={`${styles.cell} ${styles.headCell}`} scope="col">
                    Result
                  </th>
                </tr>
              </thead>
              <tbody>
                {playedMatches.map((line) => {
                  const atHome = line.venue === "home";
                  const opponent = atHome
                    ? line.match.away_team
                    : line.match.home_team;
                  return (
                    <tr key={line.match.id}>
                      <th
                        className={`${styles.cell} ${styles.stickyColumn} ${styles.dateCell}`}
                        scope="row"
                      >
                        {formatMadridDate(line.match.kickoff_date)}
                      </th>
                      <td className={`${styles.cell} ${styles.numeric}`}>
                        {formatCount(line.match.matchday)}
                      </td>
                      <td className={styles.cell}>{atHome ? "Home" : "Away"}</td>
                      <td className={`${styles.cell} ${styles.clubCell}`}>
                        <Link href={`/teams/${opponent.id}`}>{opponent.name}</Link>
                      </td>
                      <td className={`${styles.cell} ${styles.numeric}`}>
                        {`${formatCount(line.goals_for)}-${formatCount(
                          line.goals_against,
                        )}`}
                      </td>
                      <td className={styles.cell}>
                        {line.points === 3 ? "W" : line.points === 1 ? "D" : "L"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section aria-labelledby="next-fixtures" className={styles.section}>
        <h2 id="next-fixtures" className={styles.sectionTitle}>
          Next fixtures
        </h2>
        {focus.upcoming.length === 0 ? (
          <EmptyState title="No upcoming fixtures" command="make ingest">
            <p>
              The API knows no fixture of this season still to be played, so
              there is no next match to preview.
            </p>
          </EmptyState>
        ) : (
          <ul className={styles.fixtures}>
            {focus.upcoming.map((fixture) => (
              <Fixture key={fixture.match.id} fixture={fixture} />
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

/**
 * One fixture still to come, with its stored forecast when it has one.
 *
 * The forecast is looked up by model rather than by position: the API sends one
 * brief per model that has a stored prediction, and only the Dixon-Coles brief
 * is the club-level forecast this page shows. A fixture without one says so
 * instead of drawing a bar of nothing.
 */
function Fixture(props: { readonly fixture: UpcomingFixture }): ReactElement {
  const { match, predictions } = props.fixture;
  const brief = predictions.find(
    (prediction) => prediction.model === "dixon_coles",
  );

  return (
    <li className={styles.fixture}>
      <p className={styles.fixtureWhen}>
        <time dateTime={match.kickoff_date}>
          {formatMadridDate(match.kickoff_date)}
        </time>
        , {formatMadridDateTime(match.kickoff_time)}, matchday{" "}
        {formatCount(match.matchday)}
      </p>
      <p className={styles.fixtureTeams}>
        <Link href={`/teams/${match.home_team.id}`}>{match.home_team.name}</Link>
        <span className={styles.versus}> v </span>
        <Link href={`/teams/${match.away_team.id}`}>{match.away_team.name}</Link>
      </p>
      {brief === undefined ? (
        <p className={styles.note}>
          No Dixon-Coles forecast is stored for this fixture yet, so there are no
          probabilities to show.
        </p>
      ) : (
        <ProbabilityBar
          id={`fixture-${match.id}`}
          title={`${match.home_team.name} v ${match.away_team.name}: outcome probabilities`}
          home={{
            name: match.home_team.name,
            probability: brief.probabilities.home,
          }}
          draw={{ probability: brief.probabilities.draw }}
          away={{
            name: match.away_team.name,
            probability: brief.probabilities.away,
          }}
        />
      )}
    </li>
  );
}
