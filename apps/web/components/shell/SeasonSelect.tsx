/*
 * The season filter: a client component because changing it navigates, while
 * the page it filters stays a Server Component.
 */
"use client";

import { useRouter } from "next/navigation";
import type { ChangeEvent, ReactElement } from "react";

import styles from "./shell.module.css";

/** One season as the selector offers it. */
export type SeasonOption = {
  readonly code: string;
  readonly startYear: number;
};

export type SeasonSelectProps = {
  readonly options: readonly SeasonOption[];
  readonly value: string | null;
  readonly path: string;
};

/**
 * The season filter of a page.
 *
 * The current query string is read from the browser and only the `season`
 * parameter is replaced, so the other filters survive a change. `useSearchParams`
 * would suspend the whole route on first render, which is why this reads
 * `window.location.search` in the handler instead of during render.
 *
 * Without JavaScript the `noscript` list of plain links does the same job, and
 * it is always in the markup so a reader who enabled neither still sees the
 * options.
 */
export function SeasonSelect({
  options,
  value,
  path,
}: SeasonSelectProps): ReactElement {
  const router = useRouter();

  // Newest first, and copied rather than sorted in place: the caller's array is
  // not this component's to reorder.
  const ordered = [...options].sort((a, b) => b.startYear - a.startYear);

  function handleChange(event: ChangeEvent<HTMLSelectElement>): void {
    const params = new URLSearchParams(window.location.search);
    if (event.target.value === "") {
      // The empty option means "whatever the page defaults to", so it removes
      // the parameter instead of pinning the current season.
      params.delete("season");
    } else {
      params.set("season", event.target.value);
    }
    const query = params.toString();
    router.push(query === "" ? path : `${path}?${query}`);
  }

  return (
    <>
      <label className={styles.field}>
        <span className={styles.fieldLabel}>Season</span>
        <select
          className={styles.select}
          onChange={handleChange}
          value={value ?? ""}
        >
          {value === null ? <option value="">Season</option> : null}
          {ordered.map((option) => (
            <option key={option.code} value={option.code}>
              {option.code}
            </option>
          ))}
        </select>
      </label>
      <noscript className={styles.noscript}>
        Season:
        <ul className={styles.noscriptList}>
          {ordered.map((option) => (
            <li key={option.code}>
              <a href={`${path}?season=${encodeURIComponent(option.code)}`}>
                {option.code}
              </a>
            </li>
          ))}
        </ul>
      </noscript>
    </>
  );
}
