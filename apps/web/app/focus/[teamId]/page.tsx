/**
 * The team focus page: one club's season, opening on its story and the four
 * sections that explain it.
 *
 * The route is `/focus/[teamId]` and the page owns no navigation of its own: the
 * layout supplies the `main` landmark, the footer and the API status line, so
 * this file renders a plain block. The team is a path segment, the season a
 * query parameter, and a team chosen from the shared selector arrives as
 * `?teamId=` -- the page turns that into a redirect so the address bar and the
 * page always name the same club.
 *
 * Everything below the header streams: the story card and each section read
 * their own data and render their own empty or problem state, so one failed
 * request cannot take the rest of the page down. The section nav points at the
 * wrapper of each boundary rather than at the section itself, because an anchor
 * has to resolve while its section is still a skeleton.
 */
import { notFound, redirect } from "next/navigation";
import { Suspense, type ReactElement } from "react";

import { SeasonSelect, TeamSelect, type SeasonOption } from "@/components/shell";
import { EmptyState, ProblemState, SectionSkeleton } from "@/components/states";
import { HowToRead, SectionNav, type HowToReadItem, type SectionNavItem } from "@/components/ui";
import { get } from "@/lib/api";
import { setFocusTeam } from "@/lib/focus-actions";
import { firstValue, parseSeason, type SearchParams } from "@/lib/params";

import type { TeamFocus } from "./data";
import { DiagnosisSection, SECTION_LABEL as DIAGNOSIS_LABEL } from "./diagnosis-section";
import {
  NextMatchSection,
  SECTION_LABEL as NEXT_MATCH_LABEL,
} from "./next-match-section";
import { RaceSection, SECTION_LABEL as RACE_LABEL } from "./race-section";
import { SECTION_LABEL as STORY_LABEL, StorySection } from "./story-section";
import { TrendSection, SECTION_LABEL as TREND_LABEL } from "./trend-section";
import styles from "./page.module.css";

export const dynamic = "force-dynamic";

type FocusPageProps = {
  readonly params: Promise<{ readonly teamId: string }>;
  readonly searchParams: Promise<SearchParams>;
};

/** The anchor ids the section nav points at, in the order the page prints them. */
const NAV_SECTIONS: readonly SectionNavItem[] = [
  { id: "diagnosis", label: DIAGNOSIS_LABEL },
  { id: "race", label: RACE_LABEL },
  { id: "next", label: NEXT_MATCH_LABEL },
  { id: "trend", label: TREND_LABEL },
];

/**
 * The reading guide the page closes with.
 *
 * The four terms are the page's own vocabulary, and every definition is a rule
 * of the API rather than a count of this dataset, so the guide does not go stale
 * when the season moves on.
 */
const HOW_TO_READ: readonly HowToReadItem[] = [
  {
    term: "Market expected points",
    definition:
      "For each played match the pre-closing odds give a win, draw and loss chance, and 3 × the win chance plus the draw chance is what the market thought the match was worth. Summed over a season, the total is what a club with these prices was expected to earn.",
  },
  {
    term: "Relegation chance",
    definition:
      "The share of stored simulation runs in which the club finishes in the relegation places, with every remaining fixture simulated from the model's stored probabilities.",
  },
  {
    term: "Survival line",
    definition:
      "The median final points total of the highest relegated place over the completed seasons the relegation race counts, which is what staying up has usually cost.",
  },
  {
    term: "Swing",
    definition:
      "What the club's stored probabilities would be if its next match ended in a win, a draw or a loss, each read from the simulated runs that produced that result and compared with the same run's numbers for the season as it stands.",
  },
];

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
 * The page heading, rendered on every path so a reader always knows which page
 * they are looking at.
 *
 * The club name is unknown when the focus read itself failed, and the heading
 * then names the page rather than a club it cannot name. The eyebrow names the
 * season once the page has resolved one, because the heading above the sections
 * describes that season and no other.
 */
function PageHeading(props: {
  readonly teamName: string | null;
  readonly season: string | null;
}): ReactElement {
  const { teamName, season } = props;
  return (
    <header className={styles.header}>
      <p className={styles.eyebrow}>
        {season === null ? "Team focus" : `Team focus · ${season}`}
      </p>
      <h1 className={styles.title}>{teamName ?? "Team focus"}</h1>
    </header>
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

  const focus: TeamFocus = focusResult.data;
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
        <p className={styles.eyebrow}>{`Team focus · ${season}`}</p>
        <h1 className={styles.title}>{focus.team.name}</h1>
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

      <Suspense fallback={<SectionSkeleton label={STORY_LABEL} rows={3} />}>
        <StorySection
          outlook={focus.outlook}
          season={season}
          splits={focus.splits}
          standing={focus.standing}
          teamId={teamId}
        />
      </Suspense>

      <SectionNav sections={NAV_SECTIONS} />

      <div className={styles.anchor} id="diagnosis">
        <Suspense
          fallback={<SectionSkeleton label={DIAGNOSIS_LABEL} rows={4} />}
        >
          <DiagnosisSection
            season={season}
            seasonOptions={options}
            teamId={teamId}
          />
        </Suspense>
      </div>
      <div className={styles.anchor} id="race">
        <Suspense fallback={<SectionSkeleton label={RACE_LABEL} rows={4} />}>
          <RaceSection
            outlook={focus.outlook}
            season={season}
            teamId={teamId}
            teamName={focus.team.name}
          />
        </Suspense>
      </div>
      <div className={styles.anchor} id="next">
        <Suspense fallback={<SectionSkeleton label={NEXT_MATCH_LABEL} />}>
          <NextMatchSection
            outlook={focus.outlook}
            season={season}
            teamId={teamId}
            teamName={focus.team.name}
            upcoming={focus.upcoming}
          />
        </Suspense>
      </div>
      <div className={styles.anchor} id="trend">
        <Suspense fallback={<SectionSkeleton label={TREND_LABEL} rows={2} />}>
          <TrendSection teamId={teamId} />
        </Suspense>
      </div>

      <HowToRead items={HOW_TO_READ} />
    </div>
  );
}
