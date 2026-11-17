/**
 * The rules the focus page's story card and swing panel share.
 *
 * Both panels answer "how is this season going" from the same stored numbers,
 * so the thresholds, the band and the sentence live here as pure functions
 * instead of being written inside two components. Nothing in this module reads
 * the network or renders: it takes the payload the API served and returns a
 * string, so a threshold can be changed in one place and a null case is decided
 * by a function a reader can follow.
 *
 * A null is unknown and never zero. A band that no stored forecast supports is
 * `none`, not "safe", and a sentence is built only from the clauses the payload
 * actually carries, so a season without a market price does not read as a
 * season that matched the market.
 */
import {
  EM_DASH,
  formatCount,
  formatPoints,
  formatSigned,
  formatValue,
} from "@/lib/format";

import type {
  RelegationRace,
  SimulationDetail,
  TeamFocus,
  TeamSeasonStats,
} from "./data";

/** One stored simulation outlook: the club's row of the run it names. */
export type FocusOutlook = TeamFocus["outlook"];

/** The thresholds one run was requested with. */
export type Qualification = SimulationDetail["run"]["qualification"];

/** The club's next fixture with the projection of each of its outcomes. */
export type NextMatchSwing = NonNullable<RelegationRace["next_match"]>;

/**
 * The band a season falls in, decided by the stored forecast it is read from.
 *
 * The order is the design's and it is a priority, not a list of alternatives: a
 * club in a relegation fight is not also "chasing a European place", even when
 * its simulated chance of one is not zero.
 */
export type StoryBand =
  | { readonly kind: "none" }
  | { readonly kind: "relegation" }
  | { readonly kind: "title" }
  | { readonly kind: "ucl"; readonly positions: number | null }
  | { readonly kind: "europe" }
  | { readonly kind: "between" };

/** The share of simulated seasons that puts a club in a band. */
const RELEGATION_BAND = 0.1;
const TITLE_BAND = 0.1;
const EUROPE_BAND = 0.25;

/** The gap in points from the market that is worth a sentence, and a tile tone. */
const MARKET_LEVEL = 1.0;

/** The venue gap in points per match that is worth a clause. */
const VENUE_GAP = 0.75;

/** The fewest matches a venue needs before its average is compared. */
const VENUE_SAMPLE = 3;

/** Return `value` with its English ordinal suffix (`17` is `17th`). */
export function ordinal(value: number): string {
  const tens = value % 100;
  if (tens >= 11 && tens <= 13) {
    return `${formatCount(value)}th`;
  }
  const units = value % 10;
  if (units === 1) {
    return `${formatCount(value)}st`;
  }
  if (units === 2) {
    return `${formatCount(value)}nd`;
  }
  if (units === 3) {
    return `${formatCount(value)}rd`;
  }
  return `${formatCount(value)}th`;
}

/**
 * Decide the band of a season from its stored outlook.
 *
 * The qualification is a second input because two of the bands name a number of
 * places, and those counts come from the stored run rather than from a literal:
 * a league whose rules change must not leave this page claiming the old ones. A
 * run that cannot be read leaves the band without counts instead of guessing.
 */
export function storyBand(
  outlook: FocusOutlook,
  qualification: Qualification | null,
): StoryBand {
  if (outlook === null) {
    return { kind: "none" };
  }
  if (outlook.p_relegation >= RELEGATION_BAND) {
    return { kind: "relegation" };
  }
  if (outlook.p_title >= TITLE_BAND) {
    return { kind: "title" };
  }
  if (outlook.p_ucl >= EUROPE_BAND) {
    return { kind: "ucl", positions: qualification?.ucl_positions ?? null };
  }
  if (outlook.p_europe >= EUROPE_BAND) {
    return { kind: "europe" };
  }
  return { kind: "between" };
}

/** The sentence a band opens with; a band no run supports says so. */
export function bandTitle(band: StoryBand): string {
  switch (band.kind) {
    case "relegation":
      return "In a relegation fight";
    case "title":
      return "In the title race";
    case "ucl":
      return band.positions === null
        ? "Chasing a Champions League place"
        : `Chasing a top-${formatCount(band.positions)} place`;
    case "europe":
      return "Chasing a European place";
    case "between":
      return "Between the European places and the drop";
    case "none":
      return "No simulation stored";
  }
}

/**
 * The name of each qualification boundary, with the stored counts.
 *
 * Without a qualification the generic names are used and no number is claimed:
 * a page that does not know how many clubs go down must not say "bottom three".
 */
export function qualificationLabels(qualification: Qualification | null): {
  readonly ucl: string;
  readonly europe: string;
  readonly relegation: string;
} {
  if (qualification === null) {
    return {
      ucl: "Champions League",
      europe: "Europe",
      relegation: "relegation",
    };
  }
  return {
    ucl: `top ${formatCount(qualification.ucl_positions)}`,
    europe: `top ${formatCount(qualification.europe_positions)}`,
    relegation: `bottom ${formatCount(qualification.relegation_positions)}`,
  };
}

/** The title of the tile that carries the band's own chance. */
export function chanceTitle(band: StoryBand): string {
  switch (band.kind) {
    case "title":
      return "Title chance";
    case "ucl":
      return band.positions === null
        ? "Champions League chance"
        : `Top-${formatCount(band.positions)} chance`;
    case "europe":
      return "Europe chance";
    case "relegation":
    case "between":
    case "none":
      return "Relegation chance";
  }
}

/** The chance the band's tile shows, or `null` when no run stored one. */
export function chanceValue(
  band: StoryBand,
  outlook: FocusOutlook,
): number | null {
  if (outlook === null) {
    return null;
  }
  switch (band.kind) {
    case "title":
      return outlook.p_title;
    case "ucl":
      return outlook.p_ucl;
    case "europe":
      return outlook.p_europe;
    case "relegation":
    case "between":
    case "none":
      return outlook.p_relegation;
  }
}

/**
 * The probability the swing panel features for each outcome.
 *
 * It follows the band, so the panel answers the question the story card raises:
 * a club in a relegation fight reads what a result does to its relegation
 * chance, and a club in the title race reads the same about the title.
 */
export function featuredField(
  band: StoryBand,
): "p_title" | "p_europe" | "p_relegation" {
  switch (band.kind) {
    case "title":
      return "p_title";
    case "ucl":
    case "europe":
      return "p_europe";
    case "relegation":
    case "between":
    case "none":
      return "p_relegation";
  }
}

/** A change of probability, in percentage points, as the tables print it. */
export function changeInPoints(delta: number): string {
  if (!Number.isFinite(delta)) {
    return EM_DASH;
  }
  return `${formatSigned(delta * 100)} pp`;
}

/** The gap between the points earned and the points the market expected. */
export function marketGap(stats: TeamSeasonStats): number | null {
  return stats.xpts_market === null ? null : stats.points - stats.xpts_market;
}

/**
 * Whether the market clause may be stated.
 *
 * It needs a priced season and a price for every played match: a comparison of
 * a whole season against the priced part of it would credit the club for
 * matches nobody priced, which is the mistake the cumulative chart's coverage
 * note exists to prevent.
 */
export function marketIsComparable(stats: TeamSeasonStats): boolean {
  return (
    stats.xpts_market !== null &&
    stats.played > 0 &&
    stats.xpts_coverage === stats.played
  );
}

/** The venue averages, when both are comparable and far enough apart. */
export function venueGap(
  splits: TeamFocus["splits"],
): { readonly home: number; readonly away: number } | null {
  const home = splits.home;
  const away = splits.away;
  if (
    home.played < VENUE_SAMPLE ||
    away.played < VENUE_SAMPLE ||
    home.points_per_game === null ||
    away.points_per_game === null
  ) {
    return null;
  }
  if (Math.abs(home.points_per_game - away.points_per_game) < VENUE_GAP) {
    return null;
  }
  return { home: home.points_per_game, away: away.points_per_game };
}

export type StorySentenceInput = {
  /** The band the stored outlook puts the season in. */
  readonly band: StoryBand;
  /** The club's table row, or `null` when the API served none. */
  readonly standing: TeamFocus["standing"];
  /** The season statistics, or `null` when the read failed or answered 404. */
  readonly stats: TeamSeasonStats | null;
  /** The venue split of the season, which the focus payload already carries. */
  readonly splits: TeamFocus["splits"];
  /** The season the page resolved. */
  readonly season: string;
};

/**
 * The sentence the story card is heading with.
 *
 * The clauses are the three facts a reader needs to place the season: the
 * position, whether the results beat the market, and which venue is the
 * problem. A clause appears only when its own gate holds, so a sentence never
 * says "level with the market" over an unpriced season or names a venue gap
 * from two matches.
 */
export function storySentence(input: StorySentenceInput): string {
  const { band, standing, stats, splits, season } = input;
  const clauses: string[] = [];

  if (standing !== null) {
    clauses.push(
      `${ordinal(standing.position)} after ${formatCount(standing.played)} matches`,
    );
  }

  if (stats !== null && marketIsComparable(stats)) {
    const gap = marketGap(stats);
    if (gap !== null) {
      clauses.push(
        Math.abs(gap) < MARKET_LEVEL
          ? "level with what the market expected"
          : `${formatPoints(Math.abs(gap))} points ${
              gap < 0 ? "below" : "above"
            } what the market expected`,
      );
    }
  }

  const venues = venueGap(splits);
  if (venues !== null) {
    clauses.push(
      `with ${venues.home < venues.away ? "fewer" : "more"} points per match at home than away (${formatValue(venues.home, 2)} vs ${formatValue(venues.away, 2)})`,
    );
  }

  const lead =
    band.kind === "none"
      ? `No simulation stored for ${season} yet`
      : bandTitle(band);

  if (clauses.length === 0) {
    return `${lead}.`;
  }
  return `${lead}: ${clauses.join(", ")}.`;
}

/** One row of the swing table, in the order the panel prints it. */
export type SwingRow = {
  readonly label: "Now" | "Win" | "Draw" | "Loss";
  /** The share of simulated runs that produced the outcome; null for "Now". */
  readonly chance: number | null;
  /** The probability of the featured metric, as the run measured it. */
  readonly featured: number;
  /** The change against the run's `current` baseline; null for "Now". */
  readonly change: number | null;
  /** The mean final points total of the same runs. */
  readonly expectedPoints: number;
};

/**
 * The four rows of the swing table, against one baseline.
 *
 * Every outcome is compared with `next_match.current`, the club's own row of the
 * same run, so the three changes share one baseline instead of each being read
 * against a different forecast. The row order is the design's: the baseline
 * first, then the three results.
 */
export function swingRows(
  swing: NextMatchSwing,
  band: StoryBand,
): readonly SwingRow[] {
  const field = featuredField(band);
  const current = swing.current;
  const rows: SwingRow[] = [
    {
      label: "Now",
      chance: null,
      featured: current[field],
      change: null,
      expectedPoints: current.expected_points,
    },
  ];
  const outcomes: readonly { label: "Win" | "Draw" | "Loss"; key: "win" | "draw" | "loss" }[] = [
    { label: "Win", key: "win" },
    { label: "Draw", key: "draw" },
    { label: "Loss", key: "loss" },
  ];
  for (const outcome of outcomes) {
    const row = swing.outcomes[outcome.key];
    rows.push({
      label: outcome.label,
      chance: row.p_outcome,
      featured: row[field],
      change: row[field] - current[field],
      expectedPoints: row.expected_points,
    });
  }
  return rows;
}

/** One line of the transposed swing table: one metric over four columns. */
export type SwingSummaryRow = {
  readonly label: string;
  readonly now: number;
  readonly win: number;
  readonly draw: number;
  readonly loss: number;
};

/**
 * The same block transposed, so every probability of one metric sits in a row.
 *
 * The labels carry the stored qualification counts, because "bottom 3" and
 * "bottom 2" are different questions and the page knows which one it asked.
 */
export function swingSummary(
  swing: NextMatchSwing,
  qualification: Qualification | null,
): readonly SwingSummaryRow[] {
  const labels = qualificationLabels(qualification);
  return [
    {
      label: `P(${labels.relegation})`,
      now: swing.current.p_relegation,
      win: swing.outcomes.win.p_relegation,
      draw: swing.outcomes.draw.p_relegation,
      loss: swing.outcomes.loss.p_relegation,
    },
    {
      label: `P(${labels.europe})`,
      now: swing.current.p_europe,
      win: swing.outcomes.win.p_europe,
      draw: swing.outcomes.draw.p_europe,
      loss: swing.outcomes.loss.p_europe,
    },
    {
      label: "P(title)",
      now: swing.current.p_title,
      win: swing.outcomes.win.p_title,
      draw: swing.outcomes.draw.p_title,
      loss: swing.outcomes.loss.p_title,
    },
    {
      label: "Expected points",
      now: swing.current.expected_points,
      win: swing.outcomes.win.expected_points,
      draw: swing.outcomes.draw.expected_points,
      loss: swing.outcomes.loss.expected_points,
    },
  ];
}
