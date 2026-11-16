/**
 * The season selector of the table page, and the rule that builds its options.
 *
 * The rule lives next to the selector because every path of the page renders
 * the same list: a reader who lands on an empty or rejected season must still
 * see the seasons they can move to, and the options must not depend on which
 * path drew them.
 */
import type { ReactElement } from "react";

import { SeasonSelect, type SeasonOption } from "@/components/shell";

import styles from "../page.module.css";

/** The path this selector serves. */
const PAGE_PATH = "/";

/**
 * Return the seasons the selector offers, newest first.
 *
 * `currentSeason` is the season the standings answer carried. When it is known,
 * later seasons are hidden: those are stored but not played, and offering them
 * would let a reader navigate to a table that cannot exist yet. When the
 * standings read failed there is no anchor, so every season is offered.
 */
export function seasonOptions(
  seasons: readonly { readonly code: string; readonly start_year: number }[],
  currentSeason: string | null,
): SeasonOption[] {
  // Copied rather than sorted in place: the API's order is not this page's to
  // reorder.
  const ordered = [...seasons].sort((a, b) => b.start_year - a.start_year);
  const currentStartYear =
    currentSeason === null
      ? null
      : (ordered.find((entry) => entry.code === currentSeason)?.start_year ??
        null);
  const listed =
    currentStartYear === null
      ? ordered
      : ordered.filter((entry) => entry.start_year <= currentStartYear);
  return listed.map((entry) => ({
    code: entry.code,
    startYear: entry.start_year,
  }));
}

export type SeasonPickerProps = {
  readonly options: readonly SeasonOption[];
  readonly value: string | null;
};

/**
 * Render the selector, or nothing when the API returned no season at all.
 *
 * The wrapper keeps the control at its own width instead of stretching it
 * across the page column, like the teams page.
 */
export function SeasonPicker(props: SeasonPickerProps): ReactElement | null {
  const { options, value } = props;
  if (options.length === 0) {
    return null;
  }
  return (
    <div className={styles.controls}>
      <SeasonSelect options={options} value={value} path={PAGE_PATH} />
    </div>
  );
}
