/**
 * The home page: the current table and, when a run exists, the projected finish.
 *
 * The page is read-only and renders on the server. Every number comes from the
 * API: the table is the stored standings and the projection is the latest
 * succeeded simulation, never a value recomputed in the browser. The shell owns
 * the `main` landmark and the API status line, so this page renders neither.
 */
import type { ReactElement } from "react";

import { EmptyState, ProblemState } from "@/components/states";
import { get, type Problem } from "@/lib/api";
import { parseSeason, type SearchParams } from "@/lib/params";

import { ProjectionSection } from "./_table/projection-section";
import { SeasonPicker, seasonOptions } from "./_table/season-picker";
import { StandingsTable } from "./_table/standings-table";
import styles from "./page.module.css";

export const dynamic = "force-dynamic";

type HomePageProps = {
  readonly searchParams: Promise<SearchParams>;
};

/**
 * The heading of the page, rendered on every path so a reader always knows
 * which page they are on, even when neither read below it succeeded.
 *
 * It is a plain block and not a `<header>` element: the shell already owns the
 * document's one header, and a second one here would be a duplicate landmark
 * candidate rather than a new region.
 */
function PageHeading(): ReactElement {
  return (
    <div className={styles.heading}>
      <h1 className={styles.title}>LaLiga Match Intelligence</h1>
      <p className={styles.intro}>
        The current La Liga table, recomputed from stored results, and — when a
        simulation exists — the projected finish from the latest succeeded
        Monte Carlo run.
      </p>
    </div>
  );
}

export default async function HomePage(
  props: HomePageProps,
): Promise<ReactElement> {
  const params = await props.searchParams;
  const requestedSeason = parseSeason(params.season);

  const [seasonsResult, standingsResult] = await Promise.all([
    get("/v1/seasons"),
    requestedSeason === null
      ? get("/v1/standings")
      : get("/v1/standings", { query: { season: requestedSeason } }),
  ]);

  // The selector is built from the seasons list, so a rejected read leaves the
  // page with no season to offer and is reported instead of drawn.
  if (!seasonsResult.ok) {
    return (
      <div className={styles.main}>
        <PageHeading />
        <ProblemState problem={seasonsResult.problem} />
      </div>
    );
  }

  const currentSeason = standingsResult.ok ? standingsResult.data.season : null;
  const options = seasonOptions(seasonsResult.data, currentSeason);

  // A 404 without a requested season means no season has a match yet: that is an
  // empty dashboard, not an error. A 404 for a named season, or any other
  // refusal, is a problem the reader can see above the selector.
  if (!standingsResult.ok) {
    if (standingsResult.status === 404 && requestedSeason === null) {
      return (
        <div className={styles.main}>
          <PageHeading />
          <SeasonPicker options={options} value={null} />
          <EmptyState title="No match data yet" command="make ingest">
            <p>
              No season has a stored result yet, so there is no table to rank.
              Ingest the fixtures and results of a season to fill this page.
            </p>
          </EmptyState>
        </div>
      );
    }
    return (
      <div className={styles.main}>
        <PageHeading />
        <SeasonPicker options={options} value={requestedSeason} />
        <ProblemState problem={standingsResult.problem} />
      </div>
    );
  }

  const standings = standingsResult.data;
  const selectorValue = requestedSeason ?? standings.season;

  // A stored season with no fixtures is a real answer with no rows, so the page
  // says which season is empty instead of drawing a table of nothing.
  if (standings.rows.length === 0) {
    return (
      <div className={styles.main}>
        <PageHeading />
        <SeasonPicker options={options} value={selectorValue} />
        <EmptyState
          title={`No results in ${standings.season} yet`}
          command="make ingest"
        >
          <p>
            The season is stored, but none of its fixtures has been played, so
            the table has no rows to rank yet.
          </p>
        </EmptyState>
      </div>
    );
  }

  // The projection is a second read because it needs the season the standings
  // answered with. A 404 is the normal state of a season nobody simulated; an
  // empty table is the same answer, because a run without team rows claims
  // nothing a page can draw.
  const simulationResult = await get("/v1/simulations/latest", {
    query: { season: standings.season },
  });
  const projection =
    simulationResult.ok && simulationResult.data.table.length > 0
      ? simulationResult.data
      : null;
  const projectionRejected =
    !simulationResult.ok && simulationResult.status !== 404;
  const projectionProblem: Problem | null = simulationResult.ok
    ? null
    : simulationResult.problem;

  return (
    <div className={styles.main}>
      <PageHeading />
      <SeasonPicker options={options} value={selectorValue} />
      <section
        aria-labelledby="current-table-heading"
        className={styles.section}
      >
        <h2 id="current-table-heading" className={styles.sectionTitle}>
          Current table
        </h2>
        <StandingsTable standings={standings} projection={projection} />
      </section>
      <section
        aria-labelledby="projected-finish-heading"
        className={styles.section}
      >
        <h2 id="projected-finish-heading" className={styles.sectionTitle}>
          Projected finish
        </h2>
        {projectionRejected ? (
          <ProblemState problem={projectionProblem} />
        ) : projection === null ? (
          <EmptyState
            title="No simulation for this season yet"
            command="make simulate"
          >
            <p>
              The table above is the current standings. A simulation adds the
              projected finish of every club: its likely positions and its
              points interval.
            </p>
          </EmptyState>
        ) : (
          <ProjectionSection
            simulation={projection}
            season={standings.season}
          />
        )}
      </section>
    </div>
  );
}
