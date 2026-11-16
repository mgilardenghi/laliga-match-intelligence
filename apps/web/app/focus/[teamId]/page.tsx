/**
 * The team focus page: one club's season, opening on the header and its five
 * streamed sections.
 *
 * The route is `/focus/[teamId]` and the page owns no navigation of its own: the
 * layout supplies the `main` landmark, the footer and the API status line, so
 * this file renders a plain block. The team is a path segment, the season a
 * query parameter, and a team chosen from the shared selector arrives as
 * `?teamId=` -- the page turns that into a redirect so the address bar and the
 * page always name the same club.
 *
 * Everything below the header streams: each section reads its own data and
 * renders its own empty or problem state, so one failed request cannot take the
 * rest of the page down.
 */
import { notFound, redirect } from "next/navigation";
import { Suspense, type ReactElement } from "react";

import { SeasonSelect, TeamSelect, type SeasonOption } from "@/components/shell";
import { EmptyState, ProblemState, SectionSkeleton } from "@/components/states";
import { get } from "@/lib/api";
import { setFocusTeam } from "@/lib/focus-actions";
import {
  EM_DASH,
  formatCount,
  formatPercent,
  formatPoints,
  formatSigned,
} from "@/lib/format";
import { firstValue, parseSeason, type SearchParams } from "@/lib/params";

import type { TeamFocus } from "./data";
import {
  NextMatchSection,
  SECTION_LABEL as NEXT_MATCH_LABEL,
} from "./next-match-section";
import { PointsSection, SECTION_LABEL as POINTS_LABEL } from "./points-section";
import { RaceSection, SECTION_LABEL as RACE_LABEL } from "./race-section";
import { SplitsSection, SECTION_LABEL as SPLITS_LABEL } from "./splits-section";
import { TrendSection, SECTION_LABEL as TREND_LABEL } from "./trend-section";
import styles from "./page.module.css";

export const dynamic = "force-dynamic";

type FocusPageProps = {
  readonly params: Promise<{ readonly teamId: string }>;
  readonly searchParams: Promise<SearchParams>;
};

/** The path this page serves, used for the selectors and the cookie form. */
function focusPath(teamId: number): string {
  return `/focus/${teamId}`;
}

/** Return a positive integer, or `null` for anything else a URL can carry. */
function positiveInteger(value: string | undefined): number | null {
  if (value === undefined || !/^\d+$/.test(value)) {
    return null;
  }
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : null;
}

/**
 * Return the seasons the selector offers, newest first.
 *
 * The rule is the teams page's: later seasons are stored but not played, and
 * offering one would let a reader navigate to a table that cannot exist yet. It
 * is copied rather than imported because `lib/` owns no season-list helper and a
 * page may not edit it.
 */
function seasonOptions(
  seasons: readonly { readonly code: string; readonly start_year: number }[],
  currentSeason: string | null,
): SeasonOption[] {
  // Newest first, and copied rather than sorted in place: the API's own order is
  // not this page's to trust for the selector.
  const ordered = [...seasons].sort((a, b) => b.start_year - a.start_year);
  const currentStartYear =
    currentSeason === null
      ? null
      : (ordered.find((entry) => entry.code === currentSeason)?.start_year ??
        null);
  const listed =
    currentStartYear === null
      ? ordered
      : ordered.filter((entry) => entry.start_year <= currentStartYear);
  return listed.map((entry) => ({
    code: entry.code,
    startYear: entry.start_year,
  }));
}

/**
 * The page heading, rendered on every path so a reader always knows which club
 * and which season they are looking at.
 *
 * The club name is unknown when the focus read itself failed, and the heading
 * then names the page rather than a club it cannot name.
 */
function PageHeading(props: {
  readonly teamName: string | null;
  readonly season: string | null;
}): ReactElement {
  const { teamName, season } = props;
  return (
    <header className={styles.header}>
      <h1 className={styles.title}>{teamName ?? "Team focus"}</h1>
      <p className={styles.intro}>
        {season === null
          ? "One club's season: its table row, the market's expected points and, when a simulation is stored, the projected finish."
          : `The ${season} season of one club: its table row, the market's expected points and, when a simulation is stored, the projected finish.`}
      </p>
    </header>
  );
}

type OutlookTilesProps = {
  readonly standing: TeamFocus["standing"];
  readonly outlook: TeamFocus["outlook"];
};

/**
 * The outlook tiles: what the table says now and what the simulation expects.
 *
 * A value the API does not know is an em dash and never a zero, so a missing
 * simulation cannot read as a season of no points and no risk.
 */
function OutlookTiles(props: OutlookTilesProps): ReactElement {
  const { standing, outlook } = props;

  return (
    <dl className={styles.tiles}>
      <div className={styles.tile}>
        <dt className={styles.term}>Position</dt>
        <dd className={styles.value}>{formatCount(standing?.position)}</dd>
      </div>
      <div className={styles.tile}>
        <dt className={styles.term}>Points</dt>
        <dd className={styles.value}>{formatCount(standing?.points)}</dd>
      </div>
      <div className={styles.tile}>
        <dt className={styles.term}>Goal difference</dt>
        <dd className={styles.value}>{formatSigned(standing?.goal_difference)}</dd>
      </div>
      <div className={styles.tile}>
        <dt className={styles.term}>Expected points</dt>
        <dd className={styles.value}>
          {outlook === null ? EM_DASH : formatPoints(outlook.expected_points)}
          <span className={styles.range}>
            {outlook === null
              ? `${EM_DASH} range`
              : `5th–95th percentile ${formatCount(outlook.points_p05)}–${formatCount(outlook.points_p95)} points`}
          </span>
        </dd>
      </div>
      <div className={styles.tile}>
        <dt className={styles.term}>P(title)</dt>
        <dd className={styles.value}>{formatPercent(outlook?.p_title)}</dd>
      </div>
      <div className={styles.tile}>
        <dt className={styles.term}>P(UCL)</dt>
        <dd className={styles.value}>{formatPercent(outlook?.p_ucl)}</dd>
      </div>
      <div className={styles.tile}>
        <dt className={styles.term}>P(Europe)</dt>
        <dd className={styles.value}>{formatPercent(outlook?.p_europe)}</dd>
      </div>
      <div className={styles.tile}>
        <dt className={styles.term}>P(relegation)</dt>
        <dd className={styles.value}>
          {formatPercent(outlook?.p_relegation)}
        </dd>
      </div>
    </dl>
  );
}

export default async function FocusPage(
  props: FocusPageProps,
): Promise<ReactElement> {
  const [routeParams, searchParams] = await Promise.all([
    props.params,
    props.searchParams,
  ]);

  const teamId = positiveInteger(routeParams.teamId);
  if (teamId === null) {
    notFound();
  }

  const requestedSeason = parseSeason(searchParams.season);

  // The shared team selector appends `?teamId=` to this page's path, so a
  // different club here means "show that club instead". The redirect happens
  // before any read, so the address bar and the page name one club from the
  // first byte, and the season the reader chose survives the move.
  const switchedTeamId = positiveInteger(firstValue(searchParams.teamId));
  if (switchedTeamId !== null && switchedTeamId !== teamId) {
    redirect(
      requestedSeason === null
        ? focusPath(switchedTeamId)
        : `${focusPath(switchedTeamId)}?season=${encodeURIComponent(requestedSeason)}`,
    );
  }

  const [seasonsResult, standingsResult, focusResult] = await Promise.all([
    get("/v1/seasons"),
    get("/v1/standings"),
    requestedSeason === null
      ? get("/v1/teams/{team_id}/focus", { path: { team_id: teamId } })
      : get("/v1/teams/{team_id}/focus", {
          path: { team_id: teamId },
          query: { season: requestedSeason },
        }),
  ]);

  // The selector is built from the seasons list, so a rejected read leaves the
  // page with nothing to offer and is reported instead of drawn.
  if (!seasonsResult.ok) {
    return (
      <div className={styles.main}>
        <PageHeading
          teamName={focusResult.ok ? focusResult.data.team.name : null}
          season={focusResult.ok ? focusResult.data.season : requestedSeason}
        />
        <ProblemState problem={seasonsResult.problem} />
      </div>
    );
  }

  // `get("/v1/standings")` answers 404 only when no season has a match yet, and
  // that is not an error: the selector is then built without an anchor.
  const currentSeason = standingsResult.ok ? standingsResult.data.season : null;
  const options = seasonOptions(seasonsResult.data, currentSeason);
  const path = focusPath(teamId);

  if (!focusResult.ok) {
    if (focusResult.status === 404) {
      // A 404 here means either an unknown club or a club that does not play the
      // season, and the team endpoint tells the two apart: a club it does not
      // know is a missing page, a known club is an empty season view.
      const teamResult = await get("/v1/teams/{team_id}", {
        path: { team_id: teamId },
      });
      if (!teamResult.ok) {
        if (teamResult.status === 404) {
          notFound();
        }
        return (
          <div className={styles.main}>
            <PageHeading teamName={null} season={requestedSeason} />
            <ProblemState problem={teamResult.problem} />
          </div>
        );
      }

      return (
        <div className={styles.main}>
          <PageHeading
            teamName={teamResult.data.name}
            season={requestedSeason}
          />
          <EmptyState
            title="No focus view for this season"
            action={
              <SeasonSelect
                options={options}
                value={requestedSeason}
                path={path}
              />
            }
          >
            <p>
              The season code may be unknown, or {teamResult.data.name} may not
              have played it. Pick a season the club played from the list.
            </p>
          </EmptyState>
        </div>
      );
    }

    return (
      <div className={styles.main}>
        <PageHeading teamName={null} season={requestedSeason} />
        <ProblemState problem={focusResult.problem} />
      </div>
    );
  }

  const focus = focusResult.data;
  // The season shown and passed on is the one the API resolved, not the raw
  // query value: a request without `?season=` still renders a season.
  const season = focus.season;

  // The team selector offers the clubs of the resolved season. A rejected read
  // leaves the selector out instead of drawing a list of no clubs, while the
  // season selector and the cookie form still work.
  const teamsResult = await get("/v1/teams", { query: { season } });
  const teamOptions = teamsResult.ok
    ? teamsResult.data.map((team) => ({ id: team.id, name: team.name }))
    : [];

  // The cookie action returns the reader to this club and season, so making a
  // club the focus team does not silently change the season on screen.
  const redirectTo = `${path}?season=${encodeURIComponent(season)}`;

  return (
    <div className={styles.main}>
      <header className={styles.header}>
        <h1 className={styles.title}>{focus.team.name}</h1>
        <p className={styles.intro}>
          The {season} season of {focus.team.name}: its table row, the points the
          market expected and, when a simulation is stored, the projected
          finish.
        </p>
        <div className={styles.controls}>
          <SeasonSelect options={options} value={season} path={path} />
          {teamsResult.ok ? (
            <TeamSelect
              options={teamOptions}
              value={teamId}
              path={path}
              paramName="teamId"
            />
          ) : null}
          <form className={styles.form} action={setFocusTeam}>
            <input type="hidden" name="teamId" value={teamId} />
            <input type="hidden" name="redirectTo" value={redirectTo} />
            <button className={styles.button} type="submit">
              Make this my focus team
            </button>
          </form>
        </div>
      </header>

      <OutlookTiles standing={focus.standing} outlook={focus.outlook} />
      {focus.outlook === null ? (
        <p className={styles.note}>
          No simulation is stored for the {season} season, so every projected
          tile above is unknown. <code>make simulate</code> stores one.
        </p>
      ) : null}

      <Suspense fallback={<SectionSkeleton label={POINTS_LABEL} />}>
        <PointsSection teamId={teamId} season={season} seasonOptions={options} />
      </Suspense>
      <Suspense
        fallback={<SectionSkeleton label={SPLITS_LABEL} rows={4} />}
      >
        <SplitsSection teamId={teamId} season={season} seasonOptions={options} />
      </Suspense>
      <Suspense fallback={<SectionSkeleton label={TREND_LABEL} rows={2} />}>
        <TrendSection teamId={teamId} />
      </Suspense>
      <Suspense fallback={<SectionSkeleton label={RACE_LABEL} rows={4} />}>
        <RaceSection
          teamId={teamId}
          season={season}
          teamName={focus.team.name}
          outlook={focus.outlook}
        />
      </Suspense>
      <Suspense fallback={<SectionSkeleton label={NEXT_MATCH_LABEL} />}>
        <NextMatchSection
          teamId={teamId}
          season={season}
          teamName={focus.team.name}
          upcoming={focus.upcoming}
        />
      </Suspense>
    </div>
  );
}
