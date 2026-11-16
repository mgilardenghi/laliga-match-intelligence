/*
 * The team filter: a client component because changing it navigates, while the
 * page it filters stays a Server Component.
 */
"use client";

import { useRouter } from "next/navigation";
import type { ChangeEvent, ReactElement } from "react";

import styles from "./shell.module.css";

/** One team as the selector offers it. */
export type TeamOption = {
  readonly id: number;
  readonly name: string;
};

export type TeamSelectProps = {
  readonly options: readonly TeamOption[];
  readonly value: number | null;
  readonly path: string;
  readonly paramName?: string;
};

/**
 * The team filter of a page.
 *
 * It is the season selector's sibling: the browser's own query string is
 * amended and only this parameter is replaced, so the other filters survive a
 * change. The parameter name is a prop because two pages may track a team under
 * different names, and a page that guesses wrong would silently drop the filter.
 */
export function TeamSelect({
  options,
  value,
  path,
  paramName = "teamId",
}: TeamSelectProps): ReactElement {
  const router = useRouter();

  // Alphabetical by name, copied rather than sorted in place: the caller's
  // array is not this component's to reorder.
  const ordered = [...options].sort((a, b) => a.name.localeCompare(b.name));

  function handleChange(event: ChangeEvent<HTMLSelectElement>): void {
    const params = new URLSearchParams(window.location.search);
    if (event.target.value === "") {
      params.delete(paramName);
    } else {
      params.set(paramName, event.target.value);
    }
    const query = params.toString();
    router.push(query === "" ? path : `${path}?${query}`);
  }

  return (
    <>
      <label className={styles.field}>
        <span className={styles.fieldLabel}>Team</span>
        <select
          className={styles.select}
          onChange={handleChange}
          value={value === null ? "" : String(value)}
        >
          {value === null ? <option value="">Team</option> : null}
          {ordered.map((option) => (
            <option key={option.id} value={option.id}>
              {option.name}
            </option>
          ))}
        </select>
      </label>
      <noscript className={styles.noscript}>
        Team:
        <ul className={styles.noscriptList}>
          {ordered.map((option) => (
            <li key={option.id}>
              <a href={`${path}?${paramName}=${option.id}`}>{option.name}</a>
            </li>
          ))}
        </ul>
      </noscript>
    </>
  );
}
