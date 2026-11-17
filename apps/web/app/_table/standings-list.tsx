/**
 * The phone version of the home page's standings table.
 *
 * Below the 60rem breakpoint the table is wider than the screen, so the same
 * rows are drawn as a list of native disclosures: a summary always shows the
 * rank, the club, its points and its last five results, and the panel carries
 * the detail that a table row can only show by scrolling sideways. Every value
 * comes from the stored standings and the stored run, never recomputed here,
 * so the list and the table cannot disagree about a club.
 *
 * A row opens because `<details>` is a browser control: there is no client
 * state, and the only link sits in the panel, never in the summary.
 */
import Link from "next/link";
import type { ReactElement } from "react";

import { FormStrip } from "@/components/charts";
import { MetricHelp } from "@/components/ui";
import type { GetData } from "@/lib/api";
import {
  formatCount,
  formatPercent,
  formatPoints,
  formatSigned,
} from "@/lib/format";

import {
  chanceDefinitions,
  finalPointsDefinition,
  ordinal,
  pct0,
  type ChanceDefinitions,
  type ProjectedRow,
} from "./derive";
import { ZONE_LABELS, zoneOf, type Qualification, type Zone } from "./zone";
import styles from "../page.module.css";

/** The class that paints one zone's mark, and its `--zone-mark` token. */
const ZONE_CLASS: Record<Zone, string> = {
  ucl: styles.zoneUcl,
  europe: styles.zoneEurope,
  relegation: styles.zoneRelegation,
};

/** A finish below one percent is not worth a line of the summary sentence. */
const LIKELIHOOD_THRESHOLD = 0.01;

/** How many finishes the sentence lists. */
const LIKELIEST_COUNT = 3;

/** One definition the panel can show, with its label and its value. */
type ListMetric = {
  readonly term: string;
  readonly definition: string;
  readonly value: string;
};

/** The five definitions of one run: the four chances and the final points. */
type ListDefinitions = ChanceDefinitions & {
  readonly finalPoints: string;
};

/**
 * Return the sentence that names a club's likeliest finishing positions.
 *
 * Only finishes at or above one percent are listed, highest probability first
 * and lower position first on a tie, so the sentence is stable for one stored
 * run. A run whose positions all stay under one percent says so instead of
 * naming a finish the simulation almost never reached.
 */
function likeliestFinishes(row: ProjectedRow): string {
  const ranked = row.position_probabilities
    .map((probability, index) => ({ position: index + 1, probability }))
    .filter(
      (entry) =>
        Number.isFinite(entry.probability) &&
        entry.probability >= LIKELIHOOD_THRESHOLD,
    )
    .sort((a, b) =>
      a.probability === b.probability
        ? a.position - b.position
        : b.probability - a.probability,
    )
    .slice(0, LIKELIEST_COUNT);
  if (ranked.length === 0) {
    return "Likeliest finishes: no position reaches 1%.";
  }
  const listed = ranked
    .map((entry) => `${ordinal(entry.position)} ${pct0(entry.probability)}`)
    .join(", ");
  return `Likeliest finishes: ${listed}.`;
}

/**
 * Return the five metrics of the panel, in reading order.
 *
 * The terms are the labels the table's headers carry, and the definitions come
 * from the same helpers, so one metric never means two things on this page. A
 * club the run does not mention keeps every term and its help, and its value
 * is an em dash rather than a zero the run never produced.
 */
function listMetrics(options: {
  readonly projected: ProjectedRow | undefined;
  readonly definitions: ListDefinitions;
  readonly ucl: number;
  readonly europe: number;
}): readonly ListMetric[] {
  const { projected, definitions, ucl, europe } = options;
  return [
    {
      term: "Final pts",
      definition: definitions.finalPoints,
      value: formatPoints(projected?.expected_points),
    },
    {
      term: "Title",
      definition: definitions.title,
      value: formatPercent(projected?.p_title, 1),
    },
    {
      term: `Top ${formatCount(ucl)}`,
      definition: definitions.ucl,
      value: formatPercent(projected?.p_ucl, 1),
    },
    {
      term: `Top ${formatCount(europe)}`,
      definition: definitions.europe,
      value: formatPercent(projected?.p_europe, 1),
    },
    {
      term: "Relegated",
      definition: definitions.rel,
      value: formatPercent(projected?.p_relegation, 1),
    },
  ];
}

export type StandingsListProps = {
  readonly standings: GetData<"/v1/standings">;
  /** The latest succeeded run, or `null` when the season has none. */
  readonly projection: GetData<"/v1/simulations/latest"> | null;
};

export function StandingsList(props: StandingsListProps): ReactElement {
  const { standings, projection } = props;
  const run = projection === null ? null : projection.run;
  const qualification: Qualification | null =
    run === null ? null : run.qualification;
  const ucl = qualification === null ? 0 : qualification.ucl_positions;
  const europe = qualification === null ? 0 : qualification.europe_positions;
  const relegation =
    qualification === null ? 0 : qualification.relegation_positions;
  const definitions: ListDefinitions | null =
    run === null
      ? null
      : {
          finalPoints: finalPointsDefinition(run.n_runs),
          ...chanceDefinitions({
            ucl,
            europe,
            relegation,
            nRuns: run.n_runs,
          }),
        };
  const projectedRows = new Map<number, ProjectedRow>(
    projection === null
      ? []
      : projection.table.map((row) => [row.team.id, row] as const),
  );
  const teamCount = standings.rows.length;

  return (
    <div className={styles.listWrap} data-phone-list>
      {/* The header names the four visible columns for a sighted reader only:
          every summary carries the same facts, so a screen reader has nothing
          to gain from it. It holds no control. */}
      <div className={styles.listHeader} data-phone-header aria-hidden="true">
        <span>#</span>
        <span>Club</span>
        <span>Pts</span>
        <span>Last 5</span>
      </div>
      <ol className={styles.list}>
        {standings.rows.map((row) => {
          const zone =
            qualification === null
              ? null
              : zoneOf(row.position, teamCount, qualification);
          const projected = projectedRows.get(row.team.id);
          const metrics =
            definitions === null
              ? null
              : listMetrics({ projected, definitions, ucl, europe });
          return (
            <li
              className={zone === null ? undefined : ZONE_CLASS[zone]}
              data-zone={zone ?? undefined}
              key={row.team.id}
            >
              <details className={styles.listDetails}>
                <summary className={styles.listSummary}>
                  <span className={styles.listPos} data-cell="pos">
                    {row.position}
                  </span>
                  {/* The zone is text as well as colour. It stays in the
                      summary so it is read with the rank it belongs to. */}
                  <span className={styles.visuallyHidden}>
                    {zone === null ? "No qualification place" : ZONE_LABELS[zone]}
                  </span>
                  <span className={styles.listClub} data-cell="club">
                    {row.team.name}
                  </span>
                  <span className={styles.listPoints} data-cell="pts">
                    {formatCount(row.points)}
                  </span>
                  {/* The club identifier in the id keeps every strip's ids
                      unique: two clubs can share one form string. */}
                  <FormStrip
                    compact
                    id={`form-list-${row.team.id}`}
                    results={row.form.slice(0, 5)}
                  />
                </summary>
                <div className={styles.listPanel}>
                  <p className={styles.listLine}>
                    {`${formatCount(row.won)}-${formatCount(
                      row.drawn,
                    )}-${formatCount(row.lost)} · goals ${formatCount(
                      row.goals_for,
                    )}–${formatCount(
                      row.goals_against,
                    )} · GD ${formatSigned(row.goal_difference)} · played ${formatCount(
                      row.played,
                    )}`}
                  </p>
                  {metrics === null ? null : (
                    <dl className={styles.listMetrics}>
                      {metrics.map((metric) => (
                        <div className={styles.listMetricRow} key={metric.term}>
                          <dt className={styles.listMetricTerm}>
                            <span>{metric.term}</span>
                            <MetricHelp
                              definition={metric.definition}
                              label={metric.term}
                            />
                          </dt>
                          <dd className={styles.listMetricValue}>
                            {metric.value}
                          </dd>
                        </div>
                      ))}
                    </dl>
                  )}
                  {projected === undefined ? null : (
                    <p className={styles.listLine}>
                      {likeliestFinishes(projected)}
                    </p>
                  )}
                  <Link
                    className={styles.listClubLink}
                    href={`/teams/${row.team.id}`}
                  >
                    Club page
                  </Link>
                </div>
              </details>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
