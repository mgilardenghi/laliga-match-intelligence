/**
 * The home page: the current table and, when a run exists, the projected finish.
 *
 * The page is read-only and renders on the server. Every number comes from the
 * API: the table is the stored standings and the projection is the latest
 * succeeded simulation, never a value recomputed in the browser. The shell owns
 * the `main` landmark and the API status line, so this page renders neither.
 *
 * The loaded path is a sequence of sections a reader can jump between: the race
 * at a glance, the table, the projected finish and a guide to the numbers. Each
 * section exists only when the read behind it succeeded, and the section nav
 * lists exactly the sections that rendered.
 */
import Link from "next/link";
import type { ReactElement, ReactNode } from "react";

import { EmptyState, ProblemState } from "@/components/states";
import {
  HowToRead,
  Legend,
  SectionNav,
  type HowToReadItem,
  type LegendItem,
  type SectionNavItem,
} from "@/components/ui";
import { get, type GetData, type Problem } from "@/lib/api";
import { formatCount } from "@/lib/format";
import { modelLabel } from "@/lib/labels";
import { parseSeason, type SearchParams } from "@/lib/params";

import { parseColumns, type ColumnsView } from "./_table/columns";
import { pct0, rankByChance } from "./_table/derive";
import { ProjectionSection } from "./_table/projection-section";
import { RaceGlance } from "./_table/race-glance";
import { SeasonPicker, seasonOptions } from "./_table/season-picker";
import { StandingsList } from "./_table/standings-list";
import { StandingsTable } from "./_table/standings-table";
import styles from "./page.module.css";

export const dynamic = "force-dynamic";

type HomePageProps = {
  readonly searchParams: Promise<SearchParams>;
};

type Projection = GetData<"/v1/simulations/latest">;

/**
 * The heading of the page, rendered on every path so a reader always knows
 * which page they are on, even when neither read below it succeeded.
 *
 * It is a plain block and not a `<header>` element: the shell already owns the
 * document's one header, and a second one here would be a duplicate landmark
 * candidate rather than a new region.
 *
 * The loaded path passes the season it drew, the line about the run and the
 * season selector, so the selector sits beside the text on a laptop and wraps
 * under it on a phone. Every other path keeps today's intro and renders the
 * selector itself, below the heading.
 */
function PageHeading(props: {
  readonly seasonText?: string | null;
  readonly subline?: string | null;
  readonly controls?: ReactNode;
}): ReactElement {
  const { seasonText = null, subline = null, controls } = props;
  return (
    <div className={styles.headingRow}>
      <div className={styles.headingText}>
        <h1 className={styles.title}>LaLiga Match Intelligence</h1>
        {seasonText === null ? (
          <p className={styles.intro}>
            The current La Liga table, recomputed from stored results, and — when
            a simulation exists — the projected finish from the latest succeeded
            Monte Carlo run.
          </p>
        ) : (
          <>
            <p className={styles.seasonLine}>{seasonText}</p>
            {subline === null ? null : (
              <p className={styles.subline}>{subline}</p>
            )}
          </>
        )}
      </div>
      {controls === undefined ? null : (
        <div className={styles.headingControls}>{controls}</div>
      )}
    </div>
  );
}

/**
 * Return the line under the season: what the table counts and, with a run, what
 * the projection rests on.
 *
 * The last complete matchday is nullable in the API, so its clause appears only
 * when the run stored a number; a run without one does not claim an unknown
 * round.
 */
function sublineOf(
  standings: GetData<"/v1/standings">,
  projection: Projection | null,
): string {
  const resultWord = standings.matches_counted === 1 ? "result" : "results";
  const counted = `After ${formatCount(standings.matches_counted)} ${resultWord}`;
  if (projection === null) {
    return counted;
  }
  const { run } = projection;
  const matchday =
    typeof run.last_complete_matchday === "number"
      ? ` (last complete matchday ${run.last_complete_matchday})`
      : "";
  return `${counted} · projection from ${formatCount(
    run.n_runs,
  )} simulated seasons as of ${run.as_of_date}${matchday}`;
}

/**
 * Return the reading guide of a run, in the order the page reads.
 *
 * The third item explains the chances through the club the run is most certain
 * about, so the percentage is tied to the seasons it counts rather than to a
 * claim about the squad. All text is read from the stored run; nothing here
 * describes a model the API did not name.
 */
function guideOf(projection: Projection): HowToReadItem[] {
  const { run, table } = projection;
  const ucl = run.qualification.ucl_positions;
  const leader = rankByChance(table, (row) => row.p_relegation)[0];
  return [
    {
      term: "The simulation",
      definition: `Each remaining fixture gets score probabilities from the ${modelLabel(run.model)} model; the season is played out ${formatCount(run.n_runs)} times.`,
    },
    {
      term: "Final pts",
      definition: "The average final total; it includes today's points.",
    },
    {
      term: `Title · Top ${ucl} · Relegated`,
      definition: `A chance counts seasons, not strength: ${
        leader.team.name
      }'s ${pct0(leader.p_relegation)} relegated means it went down in ${Math.round(
        leader.p_relegation * 100,
      )} of every 100 simulated seasons.`,
    },
  ];
}

export default async function HomePage(
  props: HomePageProps,
): Promise<ReactElement> {
  const params = await props.searchParams;
  const requestedSeason = parseSeason(params.season);
  const columnsView: ColumnsView = parseColumns(params.columns);

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
  // answered with. A 404 is the normal state of a season nobody simulated. A
  // successful answer without a single team row is not that state: the run is
  // stored, so the page reports an incomplete projection instead of asking for
  // a run the season already has.
  const simulationResult = await get("/v1/simulations/latest", {
    query: { season: standings.season },
  });
  const projection: Projection | null =
    simulationResult.ok && simulationResult.data.table.length > 0
      ? simulationResult.data
      : null;
  const projectionIncomplete =
    simulationResult.ok && simulationResult.data.table.length === 0;
  const projectionRejected =
    !simulationResult.ok && simulationResult.status !== 404;
  const projectionProblem: Problem | null = simulationResult.ok
    ? null
    : simulationResult.problem;

  const run = projection === null ? null : projection.run;
  const qualification = run === null ? null : run.qualification;
  const ucl = qualification === null ? 0 : qualification.ucl_positions;
  const europe = qualification === null ? 0 : qualification.europe_positions;
  const relegation =
    qualification === null ? 0 : qualification.relegation_positions;

  // Every target below exists on this path, and the nav names only those: a
  // season without a run has no race and no reading guide to jump to.
  const sections: SectionNavItem[] = [
    ...(run === null
      ? []
      : [{ id: "race", label: "Race at a glance" }]),
    { id: "table", label: "Table" },
    { id: "projection", label: "Projection" },
    ...(run === null ? [] : [{ id: "how-to-read", label: "How to read" }]),
  ];

  const legendItems: LegendItem[] =
    run === null
      ? []
      : [
          { token: "zone-ucl", label: `Champions League (top ${ucl})` },
          ...(europe > ucl
            ? [
                {
                  token: "zone-europe" as const,
                  label: `Europe (places ${ucl + 1}–${europe})`,
                },
              ]
            : []),
          { token: "zone-relegation", label: `Relegation (bottom ${relegation})` },
        ];

  // The switch keeps the season, so widening the table never loses the season
  // the reader arrived on.
  const seasonQuery = encodeURIComponent(standings.season);
  const columnsHref =
    columnsView === "all"
      ? `/?season=${seasonQuery}`
      : `/?columns=all&season=${seasonQuery}`;

  return (
    <div className={styles.main}>
      <PageHeading
        controls={<SeasonPicker options={options} value={selectorValue} />}
        seasonText={`La Liga ${standings.season}`}
        subline={sublineOf(standings, projection)}
      />
      <SectionNav sections={sections} />
      {projection === null ? null : (
        <RaceGlance projection={projection} standings={standings} />
      )}
      <section
        aria-labelledby="table-heading"
        className={`${styles.anchor} ${styles.section}`}
        id="table"
      >
        <h2 className={styles.sectionTitle} id="table-heading">
          {run === null
            ? "Current table"
            : "Table and end-of-season chances"}
        </h2>
        <p className={styles.hint}>
          Sorted by points. Tap{" "}
          {/* A circled i drawn by the stylesheet, not the ⓘ character: the page's
              font stack has no glyph for U+24D8, so the character reaches a
              reader as a missing-glyph box. The mark is decorative; the
              visually hidden word keeps the sentence readable aloud. */}
          <span aria-hidden="true" className={styles.infoMark} />
          <span className={styles.visuallyHidden}>info</span>{" "}
          on a header for its definition.
        </p>
        {run === null ? null : <Legend items={legendItems} />}
        <Link className={styles.columnsLink} href={columnsHref}>
          {columnsView === "all" ? "Fewer columns" : "All columns"}
        </Link>
        <StandingsTable
          columnsView={columnsView}
          projection={projection}
          standings={standings}
        />
        <StandingsList projection={projection} standings={standings} />
      </section>
      <section
        aria-labelledby="projection-heading"
        className={`${styles.anchor} ${styles.section}`}
        id="projection"
      >
        <h2 className={styles.sectionTitle} id="projection-heading">
          Projected finish
        </h2>
        {projectionRejected ? (
          <ProblemState problem={projectionProblem} />
        ) : projection !== null ? (
          <ProjectionSection
            simulation={projection}
            season={standings.season}
          />
        ) : projectionIncomplete ? (
          <EmptyState
            title="The latest simulation stored no projected table"
            command="make simulate"
          >
            <p>
              The run the API returned has finished but holds no team row, so it
              is not a season without a forecast and this page cannot draw one
              from it. The table above is the current standings.
            </p>
          </EmptyState>
        ) : (
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
        )}
      </section>
      {projection === null ? null : (
        <div className={styles.anchor} id="how-to-read">
          <HowToRead items={guideOf(projection)} />
        </div>
      )}
    </div>
  );
}
