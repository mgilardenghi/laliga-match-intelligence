/**
 * The standings table of the home page, with the projected columns of a run.
 *
 * The table is drawn from the standings the API computed: positions, points and
 * the form string are printed as they arrived, never re-derived here. The
 * projected columns are a left join on the club identifier, so a club the run
 * does not mention keeps its real row and shows an em dash instead of a zero.
 *
 * The column set and its order come from `columnsFor`, so the head and the body
 * cannot drift apart, and every header carries its `data-col` key so a test or a
 * style can name one column without counting cells.
 */
import Link from "next/link";
import type { ReactElement } from "react";

import { FormStrip } from "@/components/charts";
import { MetricHelp } from "@/components/ui";
import type { GetData } from "@/lib/api";
import {
  EM_DASH,
  formatCount,
  formatPercent,
  formatPoints,
  formatSigned,
} from "@/lib/format";

import {
  FORM_DEFINITION,
  chanceDefinitions,
  finalPointsDefinition,
  type ProjectedRow,
} from "./derive";
import {
  columnsFor,
  type Column,
  type ColumnKey,
  type ColumnsView,
} from "./columns";
import { ZONE_LABELS, zoneOf, type Qualification, type Zone } from "./zone";
import styles from "../page.module.css";

const PAGE_PATH = "/teams";

/** The class that paints one zone's mark, and its `--zone-mark` token. */
const ZONE_CLASS: Record<Zone, string> = {
  ucl: styles.zoneUcl,
  europe: styles.zoneEurope,
  relegation: styles.zoneRelegation,
};

/** The counters of the table: they align on their right edge. */
const NUMERIC_COLUMNS: ReadonlySet<ColumnKey> = new Set<ColumnKey>([
  "p",
  "w",
  "d",
  "l",
  "gf",
  "ga",
  "gd",
  "pts",
  "xpts",
]);

/** The four columns a run fills with a chance and a bar. */
type ChanceColumnKey = Extract<ColumnKey, "title" | "ucl" | "europe" | "rel">;

const CHANCE_FILL: Readonly<Record<ChanceColumnKey, string>> = {
  title: styles.chanceFillTitle,
  ucl: styles.chanceFillUcl,
  europe: styles.chanceFillEurope,
  rel: styles.chanceFillRel,
};

const CHANCE_VALUE: Readonly<
  Record<ChanceColumnKey, (row: ProjectedRow) => number>
> = {
  title: (row) => row.p_title,
  ucl: (row) => row.p_ucl,
  europe: (row) => row.p_europe,
  rel: (row) => row.p_relegation,
};

/** Return the classes of one header cell. */
function headClassName(key: ColumnKey): string {
  const classes = [styles.cell, styles.headCell];
  if (key === "pos") {
    // The rank header travels with the reader, like the rank cells below it.
    classes.push(styles.stickyColumn, styles.positionCell);
  } else if (NUMERIC_COLUMNS.has(key)) {
    classes.push(styles.numeric);
  }
  return classes.join(" ");
}

/**
 * Return a fraction as the width of a bar, clamped to the bar's own track.
 *
 * A value outside `0..1` would otherwise paint past the track; an unknown value
 * paints nothing.
 */
function barWidth(value: number): string {
  if (!Number.isFinite(value)) {
    return "0%";
  }
  return `${Math.min(100, Math.max(0, value * 100))}%`;
}

export type StandingsTableProps = {
  readonly standings: GetData<"/v1/standings">;
  /** The latest succeeded run, or `null` when the season has none. */
  readonly projection: GetData<"/v1/simulations/latest"> | null;
  /** Which width to draw: the laptop default or the widened `?columns=all`. */
  readonly columnsView?: ColumnsView;
};

/** The header cell of one column: its label, and its definition when it has one. */
function HeadCell(props: {
  readonly column: Column;
  readonly definition: string | undefined;
}): ReactElement {
  const { column, definition } = props;
  const label = <span data-col-label>{column.label}</span>;
  return (
    <th
      className={headClassName(column.key)}
      data-col={column.key}
      scope="col"
    >
      {/* An open definition is absolutely positioned inside the header, so it
          can never widen the column it belongs to. */}
      {definition === undefined ? (
        label
      ) : (
        <span className={styles.metricHead}>
          {label}
          <MetricHelp label={column.label} definition={definition} />
        </span>
      )}
    </th>
  );
}

/** One chance cell: the bar, then the share it draws. */
function ChanceCell(props: {
  readonly column: ChanceColumnKey;
  readonly value: number;
}): ReactElement {
  const { column, value } = props;
  return (
    <td className={styles.cell}>
      <span className={styles.chance}>
        <span className={styles.chanceBar} aria-hidden="true">
          <span
            className={CHANCE_FILL[column]}
            style={{ width: barWidth(value) }}
          />
        </span>
        <span className={styles.chanceValue}>{formatPercent(value, 1)}</span>
      </span>
    </td>
  );
}

/** One standings row, one cell per column, in the head's own order. */
function BodyCells(props: {
  readonly row: GetData<"/v1/standings">["rows"][number];
  readonly zone: Zone | null;
  /** The club's projected row, or `undefined` when the run omits the club. */
  readonly projected: ProjectedRow | undefined;
  readonly columns: readonly Column[];
}): ReactElement {
  const { row, zone, projected, columns } = props;
  return (
    <>
      {columns.map((column) => {
        switch (column.key) {
          case "pos":
            return (
              <th
                className={`${styles.cell} ${styles.stickyColumn} ${styles.positionCell}`}
                key={column.key}
                scope="row"
              >
                {/* The position is the row's header, so a screen reader reads
                    every later cell against the rank and its zone. */}
                <span className={styles.position}>{row.position}</span>
                {zone === null ? null : (
                  <span className={styles.badge}>{ZONE_LABELS[zone]}</span>
                )}
              </th>
            );
          case "club":
            return (
              <td
                className={`${styles.cell} ${styles.teamCell}`}
                key={column.key}
              >
                <Link href={`${PAGE_PATH}/${row.team.id}`}>
                  {row.team.name}
                </Link>
              </td>
            );
          case "p":
            return (
              <td
                className={`${styles.cell} ${styles.numeric}`}
                key={column.key}
              >
                {formatCount(row.played)}
              </td>
            );
          case "w":
            return (
              <td
                className={`${styles.cell} ${styles.numeric}`}
                key={column.key}
              >
                {formatCount(row.won)}
              </td>
            );
          case "d":
            return (
              <td
                className={`${styles.cell} ${styles.numeric}`}
                key={column.key}
              >
                {formatCount(row.drawn)}
              </td>
            );
          case "l":
            return (
              <td
                className={`${styles.cell} ${styles.numeric}`}
                key={column.key}
              >
                {formatCount(row.lost)}
              </td>
            );
          case "gf":
            return (
              <td
                className={`${styles.cell} ${styles.numeric}`}
                key={column.key}
              >
                {formatCount(row.goals_for)}
              </td>
            );
          case "ga":
            return (
              <td
                className={`${styles.cell} ${styles.numeric}`}
                key={column.key}
              >
                {formatCount(row.goals_against)}
              </td>
            );
          case "gd":
            return (
              <td
                className={`${styles.cell} ${styles.numeric}`}
                key={column.key}
              >
                {formatSigned(row.goal_difference)}
              </td>
            );
          case "pts":
            return (
              <td
                className={`${styles.cell} ${styles.numeric}`}
                key={column.key}
              >
                {formatCount(row.points)}
              </td>
            );
          case "form":
            return (
              <td
                className={`${styles.cell} ${styles.formCell}`}
                key={column.key}
              >
                {/* The strip derives its aria ids from its title, and two clubs
                    can share a form string; the club identifier keeps every
                    strip's ids unique in the document. The compact variant
                    omits the figure, caption and data table so a row keeps the
                    five outcome squares and stays short. */}
                <FormStrip
                  compact
                  id={`form-${row.team.id}`}
                  results={row.form.slice(0, 5)}
                />
              </td>
            );
          case "xpts":
            return (
              <td
                className={`${styles.cell} ${styles.numeric}`}
                key={column.key}
              >
                {projected === undefined
                  ? EM_DASH
                  : formatPoints(projected.expected_points)}
              </td>
            );
          default:
            // The four chance columns. A club the run does not mention keeps
            // its row and prints an em dash instead of a zero and an empty bar.
            return projected === undefined ? (
              <td className={styles.cell} key={column.key}>
                {EM_DASH}
              </td>
            ) : (
              <ChanceCell
                column={column.key}
                key={column.key}
                value={CHANCE_VALUE[column.key](projected)}
              />
            );
        }
      })}
    </>
  );
}

export function StandingsTable(props: StandingsTableProps): ReactElement {
  const { standings, projection, columnsView = "default" } = props;
  const run = projection === null ? null : projection.run;
  const qualification: Qualification | null =
    run === null ? null : run.qualification;
  const ucl = qualification === null ? 0 : qualification.ucl_positions;
  const europe = qualification === null ? 0 : qualification.europe_positions;
  const relegation =
    qualification === null ? 0 : qualification.relegation_positions;
  const columns = columnsFor({
    hasRun: run !== null,
    view: columnsView,
    ucl,
    europe,
  });
  const definitions: Partial<Record<ColumnKey, string>> =
    run === null
      ? { form: FORM_DEFINITION }
      : {
          form: FORM_DEFINITION,
          xpts: finalPointsDefinition(run.n_runs),
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
  const resultWord = standings.matches_counted === 1 ? "result" : "results";

  return (
    <div className={styles.tableWrap} data-table-desktop>
      <table className={styles.table}>
        <caption className={styles.caption}>
          {`La Liga table for ${standings.season}, after ${formatCount(
            standings.matches_counted,
          )} ${resultWord}`}
        </caption>
        <thead>
          <tr>
            {columns.map((column) => (
              <HeadCell
                column={column}
                definition={definitions[column.key]}
                key={column.key}
              />
            ))}
          </tr>
        </thead>
        <tbody>
          {standings.rows.map((row) => {
            const zone =
              qualification === null
                ? null
                : zoneOf(row.position, teamCount, qualification);
            return (
              <tr
                className={zone === null ? undefined : ZONE_CLASS[zone]}
                data-zone={zone ?? undefined}
                key={row.team.id}
              >
                <BodyCells
                  columns={columns}
                  projected={projectedRows.get(row.team.id)}
                  row={row}
                  zone={zone}
                />
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
