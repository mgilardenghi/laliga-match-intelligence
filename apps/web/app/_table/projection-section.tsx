/**
 * The projected finish of one stored run: positions and points together.
 *
 * Both charts draw stored numbers only. The heatmap reads a run's position
 * probabilities and the thresholds it was simulated with; the interval bars
 * read the stored points quantiles and the real points the run started from.
 * No threshold is drawn on the points chart, because no API field defines one:
 * a line there would be this page's opinion, not the run's answer.
 */
import type { ReactElement } from "react";

import { IntervalBars, PositionHeatmap } from "@/components/charts";
import type { GetData } from "@/lib/api";
import { EM_DASH, formatCount } from "@/lib/format";

import styles from "../page.module.css";

export type ProjectionSectionProps = {
  readonly simulation: GetData<"/v1/simulations/latest">;
  /** The season code the run belongs to, used in both chart titles. */
  readonly season: string;
};

/**
 * Return the run metadata both charts state under their drawing.
 *
 * The last complete matchday is nullable in the API and an unknown matchday is
 * an em dash here, never a zero that would read as "before the season started".
 */
function runNote(
  run: GetData<"/v1/simulations/latest">["run"],
): string {
  const matchday =
    run.last_complete_matchday === null
      ? EM_DASH
      : formatCount(run.last_complete_matchday);
  return `Monte Carlo run as of ${run.as_of_date}: ${formatCount(
    run.n_runs,
  )} simulated seasons, last complete matchday ${matchday}.`;
}

export function ProjectionSection(
  props: ProjectionSectionProps,
): ReactElement {
  const { simulation, season } = props;
  const { run, table } = simulation;
  const qualification = {
    uclPositions: run.qualification.ucl_positions,
    europePositions: run.qualification.europe_positions,
    relegationPositions: run.qualification.relegation_positions,
  };

  return (
    <div className={styles.charts}>
      <PositionHeatmap
        teams={table.map((row) => row.team.name)}
        positions={table.map((row) => row.position_probabilities)}
        qualification={qualification}
        title={`Projected finishing positions, ${season}`}
        note={runNote(run)}
      />
      <IntervalBars
        rows={table.map((row) => ({
          team: row.team.name,
          p05: row.points_quantiles.p05,
          p25: row.points_quantiles.p25,
          p50: row.points_quantiles.p50,
          p75: row.points_quantiles.p75,
          p95: row.points_quantiles.p95,
          current: row.current.points,
        }))}
        title={`Projected final points, ${season}`}
        note={`${runNote(run)} Diamonds mark current points; the table above carries each club's expected final points.`}
      />
    </div>
  );
}
