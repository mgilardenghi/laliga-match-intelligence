/**
 * "Splits and discipline": where the club's season came from, group by group.
 *
 * The three tables read one endpoint. Every average in them is the API's own
 * per-match average: the page prints the number it was given and never divides
 * anything itself, so a table cannot disagree with the payload it came from. A
 * group that played nothing has no average and shows an em dash, never a zero.
 */
import type { ReactElement } from "react";

import { SeasonSelect, type SeasonOption } from "@/components/shell";
import { EmptyState, ProblemState } from "@/components/states";
import {
  formatCount,
  formatSigned,
  formatValue,
} from "@/lib/format";

import {
  loadSeasonStats,
  type SeasonRecord,
  type TeamSeasonStats,
} from "./data";
import styles from "./page.module.css";

/** The label the `<Suspense>` boundary of this section shows while it reads. */
export const SECTION_LABEL = "Splits and discipline";

export type SplitsSectionProps = {
  readonly teamId: number;
  /** The season the page resolved, never the raw query value. */
  readonly season: string;
  /** The seasons the selector offers when this one has no data. */
  readonly seasonOptions: readonly SeasonOption[];
};

/** One row of a record table: the group it covers and the counters it earned. */
type RecordRow = {
  readonly label: string;
  readonly record: SeasonRecord;
};

/** The shared cell element of the numeric columns. */
function NumberCell(props: { readonly children: string }): ReactElement {
  return (
    <td className={`${styles.cell} ${styles.numeric}`}>{props.children}</td>
  );
}

/**
 * One record table: the venue split or the tier split.
 *
 * The two tables hold the same counters over different groups of matches, so
 * they are one component with two captions instead of two copies of the same
 * nine columns.
 */
function RecordTable(props: {
  readonly caption: string;
  readonly rowHeader: string;
  readonly rows: readonly RecordRow[];
}): ReactElement {
  const { caption, rowHeader, rows } = props;

  return (
    <div className={styles.tableWrap}>
      <table className={styles.table}>
        <caption className={styles.caption}>{caption}</caption>
        <thead>
          <tr>
            <th className={`${styles.cell} ${styles.headCell}`} scope="col">
              {rowHeader}
            </th>
            {[
              "P",
              "W",
              "D",
              "L",
              "GF",
              "GA",
              "GD",
              "Pts",
              "PPG",
            ].map((column) => (
              <th
                className={`${styles.cell} ${styles.headCell} ${styles.numeric}`}
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
              <NumberCell>{formatCount(row.record.played)}</NumberCell>
              <NumberCell>{formatCount(row.record.won)}</NumberCell>
              <NumberCell>{formatCount(row.record.drawn)}</NumberCell>
              <NumberCell>{formatCount(row.record.lost)}</NumberCell>
              <NumberCell>{formatCount(row.record.goals_for)}</NumberCell>
              <NumberCell>{formatCount(row.record.goals_against)}</NumberCell>
              <NumberCell>
                {formatSigned(
                  row.record.goals_for - row.record.goals_against,
                )}
              </NumberCell>
              <NumberCell>{formatCount(row.record.points)}</NumberCell>
              <NumberCell>{formatValue(row.record.points_per_game, 2)}</NumberCell>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * The discipline table: the club's per-match averages beside its opponents' and
 * the league's.
 *
 * The three columns are the API's per-match averages, so the league column is
 * the baseline the two others are read against and not a computed difference.
 */
function DisciplineTable(props: {
  readonly data: TeamSeasonStats;
}): ReactElement {
  const { data } = props;
  const rows = [
    {
      label: "Yellow cards",
      own: data.per_match.for.yellow,
      conceded: data.per_match.against.yellow,
      league: data.league_avg.yellow,
    },
    {
      label: "Red cards",
      own: data.per_match.for.red,
      conceded: data.per_match.against.red,
      league: data.league_avg.red,
    },
    {
      label: "Fouls",
      own: data.per_match.for.fouls,
      conceded: data.per_match.against.fouls,
      league: data.league_avg.fouls,
    },
  ];

  return (
    <>
      <div className={styles.tableWrap}>
        <table className={styles.table}>
          <caption className={styles.caption}>
            Discipline per match, {data.season} season
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
                For
              </th>
              <th
                className={`${styles.cell} ${styles.headCell} ${styles.numeric}`}
                scope="col"
              >
                Against
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
                <NumberCell>{formatValue(row.own, 2)}</NumberCell>
                <NumberCell>{formatValue(row.conceded, 2)}</NumberCell>
                <NumberCell>{formatValue(row.league, 2)}</NumberCell>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className={styles.note}>
        Every value is a per-match average over the played matches that carry
        the statistic, so the three columns rest on the same kind of denominator
        but not necessarily on the same number of matches. A dash means no
        counted match carries the value.
      </p>
    </>
  );
}

/** The three tables, for a season with at least one played match. */
function SplitsBody(props: {
  readonly data: TeamSeasonStats;
}): ReactElement {
  const { data } = props;

  return (
    <>
      <RecordTable
        caption={`Home and away record, ${data.season} season`}
        rowHeader="Venue"
        rows={[
          { label: "Home", record: data.splits.home },
          { label: "Away", record: data.splits.away },
        ]}
      />
      <RecordTable
        caption={`Record against top, middle and bottom opponents, ${data.season} season`}
        rowHeader="Opponents"
        rows={[
          { label: "Top", record: data.tiers.top },
          { label: "Middle", record: data.tiers.middle },
          { label: "Bottom", record: data.tiers.bottom },
        ]}
      />
      <DisciplineTable data={data} />
    </>
  );
}

/**
 * The streamed section: the heading is rendered on every path, and the body is
 * decided by the read alone.
 */
export async function SplitsSection(
  props: SplitsSectionProps,
): Promise<ReactElement> {
  const { teamId, season, seasonOptions } = props;
  const result = await loadSeasonStats(teamId, season);

  return (
    <section className={styles.section} aria-labelledby="splits-heading">
      <h2 className={styles.heading} id="splits-heading">
        {SECTION_LABEL}
      </h2>
      {result.ok ? (
        result.data.played === 0 ? (
          <EmptyState title={`No played matches in ${result.data.season}`}>
            <p>
              The club has played no match of this season yet, so there is no
              venue split, no tier split and no discipline average to report.
            </p>
          </EmptyState>
        ) : (
          <SplitsBody data={result.data} />
        )
      ) : result.status === 404 ? (
        <EmptyState
          title={`No season statistics for ${season}`}
          action={
            <SeasonSelect
              options={seasonOptions}
              value={season}
              path={`/focus/${teamId}`}
            />
          }
        >
          <p>
            The API holds no statistics for this club in this season. Pick
            another season from the list.
          </p>
        </EmptyState>
      ) : (
        <ProblemState problem={result.problem} />
      )}
    </section>
  );
}
