/**
 * The current table, with the projected columns when a run exists.
 *
 * The table is drawn from the standings the API computed: positions, points and
 * the form string are printed as they arrived, never re-derived here. The
 * projected columns are a left join on the club identifier, so a club the run
 * does not mention keeps its real row and shows an em dash instead of a zero.
 */
import Link from "next/link";
import type { ReactElement } from "react";

import { FormStrip } from "@/components/charts";
import type { GetData } from "@/lib/api";
import {
  EM_DASH,
  formatCount,
  formatPercent,
  formatPoints,
  formatSigned,
} from "@/lib/format";

import { ZONE_LABELS, zoneOf, type Qualification, type Zone } from "./zone";
import styles from "../page.module.css";

const PAGE_PATH = "/teams";

/** The tint of one zone, used as the row background and the badge mark. */
const ZONE_CLASS: Record<Zone, string> = {
  ucl: styles.zoneUcl,
  europe: styles.zoneEurope,
  relegation: styles.zoneRelegation,
};

export type StandingsTableProps = {
  readonly standings: GetData<"/v1/standings">;
  /** The latest succeeded run, or `null` when the season has none. */
  readonly projection: GetData<"/v1/simulations/latest"> | null;
};

/** One projected column, so the body repeats the same five cells in order. */
function ProjectedCells(props: {
  readonly row: GetData<"/v1/simulations/latest">["table"][number] | undefined;
}): ReactElement {
  const { row } = props;
  return (
    <>
      <td className={`${styles.cell} ${styles.numeric}`}>
        {row === undefined ? EM_DASH : formatPoints(row.expected_points)}
      </td>
      <td className={`${styles.cell} ${styles.numeric}`}>
        {row === undefined ? EM_DASH : formatPercent(row.p_title, 1)}
      </td>
      <td className={`${styles.cell} ${styles.numeric}`}>
        {row === undefined ? EM_DASH : formatPercent(row.p_ucl, 1)}
      </td>
      <td className={`${styles.cell} ${styles.numeric}`}>
        {row === undefined ? EM_DASH : formatPercent(row.p_europe, 1)}
      </td>
      <td className={`${styles.cell} ${styles.numeric}`}>
        {row === undefined ? EM_DASH : formatPercent(row.p_relegation, 1)}
      </td>
    </>
  );
}

export function StandingsTable(props: StandingsTableProps): ReactElement {
  const { standings, projection } = props;
  const hasProjection = projection !== null;
  const qualification: Qualification | null =
    projection === null ? null : projection.run.qualification;
  const projectedRows = new Map(
    projection === null
      ? []
      : projection.table.map((row) => [row.team.id, row] as const),
  );
  const teamCount = standings.rows.length;
  const resultWord = standings.matches_counted === 1 ? "result" : "results";

  return (
    <div className={styles.tableWrap}>
      <table className={styles.table}>
        <caption className={styles.caption}>
          {`La Liga table for ${standings.season}, after ${formatCount(
            standings.matches_counted,
          )} ${resultWord}`}
        </caption>
        <thead>
          <tr>
            <th
              className={`${styles.cell} ${styles.headCell} ${styles.stickyColumn} ${styles.positionCell}`}
              scope="col"
            >
              Pos
            </th>
            <th className={`${styles.cell} ${styles.headCell}`} scope="col">
              Team
            </th>
            <th
              className={`${styles.cell} ${styles.headCell} ${styles.numeric}`}
              scope="col"
            >
              P
            </th>
            <th
              className={`${styles.cell} ${styles.headCell} ${styles.numeric}`}
              scope="col"
            >
              W
            </th>
            <th
              className={`${styles.cell} ${styles.headCell} ${styles.numeric}`}
              scope="col"
            >
              D
            </th>
            <th
              className={`${styles.cell} ${styles.headCell} ${styles.numeric}`}
              scope="col"
            >
              L
            </th>
            <th
              className={`${styles.cell} ${styles.headCell} ${styles.numeric} ${styles.colOptional}`}
              scope="col"
            >
              GF
            </th>
            <th
              className={`${styles.cell} ${styles.headCell} ${styles.numeric} ${styles.colOptional}`}
              scope="col"
            >
              GA
            </th>
            <th
              className={`${styles.cell} ${styles.headCell} ${styles.numeric}`}
              scope="col"
            >
              GD
            </th>
            <th
              className={`${styles.cell} ${styles.headCell} ${styles.numeric}`}
              scope="col"
            >
              Pts
            </th>
            <th className={`${styles.cell} ${styles.headCell}`} scope="col">
              Form
            </th>
            {hasProjection ? (
              <>
                <th
                  className={`${styles.cell} ${styles.headCell} ${styles.numeric}`}
                  scope="col"
                >
                  xPts
                </th>
                <th
                  className={`${styles.cell} ${styles.headCell} ${styles.numeric}`}
                  scope="col"
                >
                  P(title)
                </th>
                <th
                  className={`${styles.cell} ${styles.headCell} ${styles.numeric}`}
                  scope="col"
                >
                  P(UCL)
                </th>
                <th
                  className={`${styles.cell} ${styles.headCell} ${styles.numeric}`}
                  scope="col"
                >
                  P(Europe)
                </th>
                <th
                  className={`${styles.cell} ${styles.headCell} ${styles.numeric}`}
                  scope="col"
                >
                  P(releg.)
                </th>
              </>
            ) : null}
          </tr>
        </thead>
        <tbody>
          {standings.rows.map((row) => {
            const zone =
              qualification === null
                ? null
                : zoneOf(row.position, teamCount, qualification);
            const projectedRow = projectedRows.get(row.team.id);
            return (
              <tr
                key={row.team.id}
                data-zone={zone ?? undefined}
                className={zone === null ? undefined : ZONE_CLASS[zone]}
              >
                <th
                  className={`${styles.cell} ${styles.stickyColumn} ${styles.positionCell}`}
                  scope="row"
                >
                  <span className={styles.position}>{row.position}</span>
                  {zone === null ? null : (
                    <span className={styles.badge}>{ZONE_LABELS[zone]}</span>
                  )}
                </th>
                <td className={`${styles.cell} ${styles.teamCell}`}>
                  <Link href={`${PAGE_PATH}/${row.team.id}`}>
                    {row.team.name}
                  </Link>
                </td>
                <td className={`${styles.cell} ${styles.numeric}`}>
                  {formatCount(row.played)}
                </td>
                <td className={`${styles.cell} ${styles.numeric}`}>
                  {formatCount(row.won)}
                </td>
                <td className={`${styles.cell} ${styles.numeric}`}>
                  {formatCount(row.drawn)}
                </td>
                <td className={`${styles.cell} ${styles.numeric}`}>
                  {formatCount(row.lost)}
                </td>
                <td
                  className={`${styles.cell} ${styles.numeric} ${styles.colOptional}`}
                >
                  {formatCount(row.goals_for)}
                </td>
                <td
                  className={`${styles.cell} ${styles.numeric} ${styles.colOptional}`}
                >
                  {formatCount(row.goals_against)}
                </td>
                <td className={`${styles.cell} ${styles.numeric}`}>
                  {formatSigned(row.goal_difference)}
                </td>
                <td className={`${styles.cell} ${styles.numeric}`}>
                  {formatCount(row.points)}
                </td>
                <td className={`${styles.cell} ${styles.formCell}`}>
                  {/* The strip derives its aria ids from its title, and two
                      clubs can share a form string; the club identifier keeps
                      every strip's ids unique in the document. The compact
                      variant omits the figure, caption and data table so a
                      row keeps the five outcome squares and stays short. */}
                  <FormStrip
                    id={`form-${row.team.id}`}
                    results={row.form.slice(0, 5)}
                    compact
                  />
                </td>
                {hasProjection ? <ProjectedCells row={projectedRow} /> : null}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
