/**
 * The pure helpers behind the table page's sentences, tables and definitions.
 *
 * Nothing here renders: the module turns stored numbers into the strings and
 * orders the page uses, so the same rule serves the projected table, the phone
 * list and the "at a glance" cards. Every helper reads only its arguments, and
 * an unknown number becomes an em dash instead of a zero.
 */
import type { GetData } from "@/lib/api";
import { EM_DASH, formatCount, formatPercent } from "@/lib/format";

/** One row of a stored run's projected table. */
export type ProjectedRow = GetData<"/v1/simulations/latest">["table"][number];

/** The four chances a run stores for every club. */
export type ChanceDefinitions = Record<
  "title" | "ucl" | "europe" | "rel",
  string
>;

// Rounding thresholds: under half a percent reads as "<1%", above 99.5% as
// ">99%", so a sentence never claims a certainty the simulation did not reach.
const BELOW_ONE_PERCENT = 0.005;
const ABOVE_NINETY_NINE = 0.995;

/**
 * Return a probability as a whole percentage with the near-certain ends named.
 *
 * The input is a fraction (`0.803` renders as `80%`). A non-finite value is
 * unknown and renders as an em dash, never as `0%`.
 */
export function pct0(value: number): string {
  if (!Number.isFinite(value)) {
    return EM_DASH;
  }
  if (value < BELOW_ONE_PERCENT) {
    return "<1%";
  }
  if (value > ABOVE_NINETY_NINE) {
    return ">99%";
  }
  return formatPercent(value, 0);
}

/**
 * Return a position as an English ordinal: `1st`, `2nd`, `3rd`, `4th`, `11th`.
 *
 * A non-finite value is unknown and renders as an em dash.
 */
export function ordinal(value: number): string {
  if (!Number.isFinite(value)) {
    return EM_DASH;
  }
  const whole = Math.trunc(value);
  // The teens are all "-th": 11th, 12th and 13th, not 11st and 12nd.
  const tens = Math.abs(whole) % 100;
  const ones = Math.abs(whole) % 10;
  let suffix = "th";
  if (tens < 11 || tens > 13) {
    if (ones === 1) {
      suffix = "st";
    } else if (ones === 2) {
      suffix = "nd";
    } else if (ones === 3) {
      suffix = "rd";
    }
  }
  return `${whole}${suffix}`;
}

/**
 * Join club names into one English list.
 *
 * One name stands alone, two are joined with "and", and three or more take a
 * comma before the "and".
 */
export function joinNames(names: readonly string[]): string {
  if (names.length === 0) {
    return "";
  }
  if (names.length === 1) {
    return names[0];
  }
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

/** The chance of one row, as a finite sort key. */
function chanceKey(
  row: ProjectedRow,
  chance: (row: ProjectedRow) => number,
): number {
  const value = chance(row);
  // An unknown chance sorts last instead of scattering the order, because a
  // comparator that returns NaN leaves the order up to the engine.
  return Number.isFinite(value) ? value : Number.NEGATIVE_INFINITY;
}

/**
 * Return a copy of the rows ordered by one chance, highest first.
 *
 * Ties break on the club name and then on the club identifier, so the same run
 * always produces the same sentence. The input array is never reordered.
 */
export function rankByChance(
  rows: readonly ProjectedRow[],
  chance: (row: ProjectedRow) => number,
): ProjectedRow[] {
  return [...rows].sort((a, b) => {
    const left = chanceKey(a, chance);
    const right = chanceKey(b, chance);
    if (left > right) {
      return -1;
    }
    if (left < right) {
      return 1;
    }
    if (a.team.name !== b.team.name) {
      return a.team.name < b.team.name ? -1 : 1;
    }
    return a.team.id - b.team.id;
  });
}

/** What the compact form strip's five squares mean. */
export const FORM_DEFINITION =
  "The club's five most recent results, newest first: W win, D draw, L loss.";

/**
 * Return the definition of a club's average final points.
 *
 * The count of simulated seasons is part of the sentence: it is what makes the
 * average a simulation result rather than a projection of one season.
 */
export function finalPointsDefinition(nRuns: number): string {
  return `Average final points over the ${formatCount(
    nRuns,
  )} simulated seasons, including the points already won.`;
}

/**
 * Return the definitions of the four chance columns of one run.
 *
 * The thresholds are read from the run, never spelled here: a stored run keeps
 * meaning what it meant when it was drawn. `nRuns` travels with the other run
 * facts so every caller reads its sentences from one call; the four chance
 * sentences themselves count seasons rather than name the run.
 */
export function chanceDefinitions(options: {
  readonly ucl: number;
  readonly europe: number;
  readonly relegation: number;
  readonly nRuns: number;
}): ChanceDefinitions {
  const ucl = formatCount(options.ucl);
  const europe = formatCount(options.europe);
  const relegation = formatCount(options.relegation);
  return {
    title: "Share of simulated seasons in which the club finishes first.",
    ucl: `Share of simulated seasons in which the club finishes in the top ${ucl}, the Champions League places stored with this run.`,
    europe: `Share of simulated seasons in which the club finishes in the top ${europe}, Champions League included.`,
    rel: `Share of simulated seasons in which the club finishes in the bottom ${relegation}.`,
  };
}
