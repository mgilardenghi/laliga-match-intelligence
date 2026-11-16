/**
 * The teams index, which is also the focus-team picker.
 *
 * Every other page opens on one focus team, so the dashboard needs exactly one
 * place where that team is chosen. The index lists the clubs of a season and
 * marks the resolved focus team, and the same table carries the form that
 * stores another choice, so picking a club lands the reader back where they
 * were instead of somewhere else in the dashboard.
 */
import Link from "next/link";
import type { ReactElement } from "react";

import { SeasonSelect, type SeasonOption } from "@/components/shell";
import { EmptyState, ProblemState } from "@/components/states";
import { get } from "@/lib/api";
import { resolveFocusTeam } from "@/lib/focus";
import { setFocusTeam } from "@/lib/focus-actions";
import { firstValue, parseSeason, type SearchParams } from "@/lib/params";

import styles from "./page.module.css";

export const dynamic = "force-dynamic";

/** The path this page serves, used for links, the selector and the redirects. */
const PAGE_PATH = "/teams";

type TeamsPageProps = {
  readonly searchParams: Promise<SearchParams>;
};

/**
 * The heading of the page, rendered on every path so a reader always knows
 * which page they are on, even when the table below it could not be read.
 */
function PageHeading(): ReactElement {
  return (
    <header className={styles.header}>
      <h1 className={styles.title}>Teams</h1>
      <p className={styles.intro}>
        This page lists the clubs of one season. One of them is the focus team —
        the club the rest of the dashboard opens on — and any other club can take
        that place from here.
      </p>
    </header>
  );
}

/**
 * The season selector, or nothing when the API returned no season at all.
 *
 * It is a separate component because three paths render it: the table, the
 * empty season and the rejected season, and all three must offer the same way
 * out of the season they landed on.
 */
function SeasonPicker(props: {
  readonly options: readonly SeasonOption[];
  readonly season: string | null;
}): ReactElement | null {
  const { options, season } = props;
  if (options.length === 0) {
    return null;
  }
  return (
    <div className={styles.controls}>
      <SeasonSelect options={options} value={season} path={PAGE_PATH} />
    </div>
  );
}

export default async function TeamsPage(
  props: TeamsPageProps,
): Promise<ReactElement> {
  const params = await props.searchParams;
  const requestedSeason = parseSeason(params.season);
  const pick = firstValue(params.pick);

  const [seasonsResult, standingsResult] = await Promise.all([
    get("/v1/seasons"),
    get("/v1/standings"),
  ]);

  // The selector is built from the seasons list, so a rejected read leaves the
  // page with nothing to offer and is reported instead of drawn.
  if (!seasonsResult.ok) {
    return (
      <div className={styles.main}>
        <PageHeading />
        <ProblemState problem={seasonsResult.problem} />
      </div>
    );
  }

  // A 404 here means no season has a match yet, so the current season is simply
  // unknown rather than an error. Any other failure is unexpected: the selector
  // is left out, because a list of seasons would misrepresent what was read.
  if (!standingsResult.ok && standingsResult.status !== 404) {
    return (
      <div className={styles.main}>
        <PageHeading />
        <ProblemState problem={standingsResult.problem} />
      </div>
    );
  }

  const currentSeason = standingsResult.ok ? standingsResult.data.season : null;

  // Newest first, and copied rather than sorted in place: the API's own order is
  // not this page's to trust for the selector.
  const seasons = [...seasonsResult.data].sort(
    (a, b) => b.start_year - a.start_year,
  );
  const currentStartYear =
    seasons.find((entry) => entry.code === currentSeason)?.start_year ?? null;
  const listed =
    currentStartYear === null
      ? seasons
      : seasons.filter((entry) => entry.start_year <= currentStartYear);
  const options: SeasonOption[] = listed.map((entry) => ({
    code: entry.code,
    startYear: entry.start_year,
  }));

  // The requested season wins, then the current one, then the newest the
  // selector offers; a stack with no data at all leaves the season unknown.
  const season = requestedSeason ?? currentSeason ?? options[0]?.code ?? null;

  const [teamsResult, focusTeam] = await Promise.all([
    season === null ? get("/v1/teams") : get("/v1/teams", { query: { season } }),
    resolveFocusTeam(),
  ]);

  // An unknown season code is a refusal, not a missing page: the operator stays
  // on this page and can pick a season from the selector above the problem.
  if (!teamsResult.ok) {
    return (
      <div className={styles.main}>
        <PageHeading />
        <SeasonPicker options={options} season={season} />
        <ProblemState problem={teamsResult.problem} />
      </div>
    );
  }

  const teams = teamsResult.data;
  const redirectTo =
    season === null
      ? PAGE_PATH
      : `${PAGE_PATH}?season=${encodeURIComponent(season)}`;

  return (
    <div className={styles.main}>
      <PageHeading />
      <SeasonPicker options={options} season={season} />
      {pick === "focus" ? (
        <p className={styles.notice} role="status">
          {focusTeam === null
            ? "No focus team could be resolved, so the focus pages have nothing to open on. Pick the club you follow from the table below."
            : `Your focus team is ${focusTeam.name}. Pick another club below to change it.`}
        </p>
      ) : null}
      {teams.length === 0 ? (
        <EmptyState
          title={season === null ? "No teams yet" : `No teams in ${season}`}
          command="make ingest"
        >
          <p>
            The API lists no club for this season, because none of its fixtures
            has been loaded yet.
          </p>
        </EmptyState>
      ) : (
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <caption className={styles.caption}>
              {season === null
                ? "Every club the API returned"
                : `Clubs of the ${season} season`}
            </caption>
            <thead>
              <tr>
                <th
                  className={`${styles.cell} ${styles.headCell} ${styles.stickyColumn}`}
                  scope="col"
                >
                  Team
                </th>
                <th className={`${styles.cell} ${styles.headCell}`} scope="col">
                  Focus
                </th>
              </tr>
            </thead>
            <tbody>
              {teams.map((team) => (
                <tr key={team.id}>
                  <th
                    className={`${styles.cell} ${styles.stickyColumn} ${styles.clubCell}`}
                    scope="row"
                  >
                    <Link href={`${PAGE_PATH}/${team.id}`}>{team.name}</Link>
                  </th>
                  <td className={styles.cell}>
                    {focusTeam !== null && focusTeam.id === team.id ? (
                      <span className={styles.badge}>Your focus team</span>
                    ) : (
                      <form action={setFocusTeam}>
                        <input type="hidden" name="teamId" value={team.id} />
                        <input
                          type="hidden"
                          name="redirectTo"
                          value={redirectTo}
                        />
                        <button className={styles.button} type="submit">
                          Make this my focus team
                        </button>
                      </form>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
