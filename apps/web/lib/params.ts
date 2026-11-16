/**
 * Search-parameter parsing for Server Components.
 *
 * Next 16 hands a page `searchParams: Promise<Record<string, string | string[]
 * | undefined>>`. Every parser falls back to the caller's default instead of
 * throwing, so an invalid URL value never reaches the API and cannot produce a
 * `422`.
 */
export type SearchParams = Record<string, string | string[] | undefined>;

/** A season code, for example `2025-26`. */
export const SEASON_PATTERN: RegExp = /^\d{4}-\d{2}$/;

/** The matchdays a La Liga season can hold. */
export const MATCHDAY_RANGE: { readonly min: 1; readonly max: 46 } = {
  min: 1,
  max: 46,
};

/**
 * Return the first value of a possibly repeated parameter.
 *
 * A repeated parameter yields its first value; an absent parameter returns
 * `undefined`.
 */
export function firstValue(
  value: string | string[] | undefined,
): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

/**
 * Return a trimmed season code matching `SEASON_PATTERN`, or `null` otherwise.
 *
 * Missing, empty and malformed values all return `null`, so the caller can use
 * its default season.
 */
export function parseSeason(
  value: string | string[] | undefined,
): string | null {
  const candidate = firstValue(value)?.trim();
  return candidate !== undefined && SEASON_PATTERN.test(candidate)
    ? candidate
    : null;
}

/**
 * Return an integer inside `MATCHDAY_RANGE`, or `null` otherwise.
 *
 * Missing, non-numeric, fractional and out-of-range values all return `null`,
 * so an invalid matchday never reaches the API.
 */
export function parseMatchday(
  value: string | string[] | undefined,
): number | null {
  const candidate = firstValue(value)?.trim();
  if (candidate === undefined || !/^\d+$/.test(candidate)) {
    return null;
  }
  const matchday = Number(candidate);
  return Number.isInteger(matchday) &&
    matchday >= MATCHDAY_RANGE.min &&
    matchday <= MATCHDAY_RANGE.max
    ? matchday
    : null;
}

/** The metric subset a page asks for. */
export type MetricsSubset = "all" | "common";

/**
 * Return `"common"` only for the exact value `"common"`, else `"all"`.
 *
 * Any other value, including a repeated parameter whose first value is not
 * `"common"`, falls back to `"all"`.
 */
export function parseSubset(
  value: string | string[] | undefined,
): MetricsSubset {
  return firstValue(value) === "common" ? "common" : "all";
}
