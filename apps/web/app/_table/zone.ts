/**
 * The qualification zones of the table, derived only from a stored run.
 *
 * The competition format is not a constant of the dashboard: the thresholds are
 * stored with every simulation, so a past forecast keeps meaning what it meant
 * when it was drawn. Nothing here reads a threshold of its own; the caller
 * passes the ones the API returned.
 */

/** One qualification zone a table position can fall in. */
export type Zone = "ucl" | "europe" | "relegation";

/** The thresholds of one run, spelled as the API stores them. */
export type Qualification = {
  readonly ucl_positions: number;
  readonly europe_positions: number;
  readonly relegation_positions: number;
};

/** The visible name of each zone, used as the text badge beside a position. */
export const ZONE_LABELS: Record<Zone, string> = {
  ucl: "UCL",
  europe: "Europe",
  relegation: "Relegation",
};

/**
 * Return the zone of one table position, or `null` for an unqualified place.
 *
 * `position` counts from the top and `teamCount` is the size of the table, so
 * the relegation places are the last `relegation_positions` rows. A position
 * outside `1..teamCount`, or a threshold that is not a finite number, yields
 * `null`: an unknown qualification is not a guess.
 */
export function zoneOf(
  position: number,
  teamCount: number,
  qualification: Qualification,
): Zone | null {
  const ucl = qualification.ucl_positions;
  const europe = qualification.europe_positions;
  const relegation = qualification.relegation_positions;
  if (
    !Number.isFinite(position) ||
    !Number.isFinite(teamCount) ||
    !Number.isFinite(ucl) ||
    !Number.isFinite(europe) ||
    !Number.isFinite(relegation)
  ) {
    return null;
  }
  if (teamCount <= 0 || position < 1 || position > teamCount) {
    return null;
  }
  if (position <= ucl) {
    return "ucl";
  }
  if (position <= europe) {
    return "europe";
  }
  if (position > teamCount - relegation) {
    return "relegation";
  }
  return null;
}
