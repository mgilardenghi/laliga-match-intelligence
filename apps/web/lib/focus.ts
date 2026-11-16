import { cache } from "react";
import { cookies } from "next/headers";

import { get } from "./api/client";

/** The cookie that stores the chosen focus team's public identifier. */
export const FOCUS_COOKIE = "lmi_focus_team";

/** The team the focus pages default to when the URL and cookie decide nothing. */
export type FocusTeam = {
  readonly id: number;
  readonly name: string;
};

function positiveInteger(value: string | undefined): number | null {
  if (value === undefined || !/^\d+$/.test(value)) {
    return null;
  }
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : null;
}

/**
 * Return the configured focus-team name, defaulting to `"Valencia"`.
 *
 * The name is the stored canonical spelling; an unset variable uses the
 * documented default, and a miss in the teams list is resolved by the caller.
 */
export function focusTeamNameFromEnv(): string {
  return process.env.LMI_FOCUS_TEAM ?? "Valencia";
}

/**
 * Resolve the focus team: the cookie, then the configured name, then `null`.
 *
 * The cookie wins only when it holds a positive integer and
 * `GET /v1/teams/{team_id}` answers 200. Otherwise the environment name is
 * looked up in the teams of the current season, first exactly and then
 * case-insensitively, never by substring. A 404 or any other handled error at
 * either step returns `null`; a network failure or 5xx propagates as
 * `ApiUnavailableError`, leaving the decision to the caller.
 */
export const resolveFocusTeam = cache(
  async (): Promise<FocusTeam | null> => {
    const cookieStore = await cookies();
    const cookieTeamId = positiveInteger(cookieStore.get(FOCUS_COOKIE)?.value);
    if (cookieTeamId !== null) {
      const cookieTeam = await get("/v1/teams/{team_id}", {
        path: { team_id: cookieTeamId },
      });
      if (cookieTeam.ok) {
        return { id: cookieTeam.data.id, name: cookieTeam.data.name };
      }
    }

    const wantedName = focusTeamNameFromEnv();
    const standings = await get("/v1/standings");
    if (!standings.ok) {
      return null;
    }

    const teams = await get("/v1/teams", {
      query: { season: standings.data.season },
    });
    if (!teams.ok) {
      return null;
    }

    const exact = teams.data.find((team) => team.name === wantedName);
    if (exact !== undefined) {
      return { id: exact.id, name: exact.name };
    }

    // Second pass: the stored spelling may differ in case only. A substring
    // match is deliberately not attempted, so "Valencia" never selects another
    // club whose name merely contains it.
    const wantedLower = wantedName.toLowerCase();
    const insensitive = teams.data.find(
      (team) => team.name.toLowerCase() === wantedLower,
    );
    return insensitive === undefined
      ? null
      : { id: insensitive.id, name: insensitive.name };
  },
);

/**
 * Return the resolved focus team's name, or `null` when it is unknown.
 *
 * This lenient wrapper for the header never throws: every failure, including a
 * network failure or a 5xx, is reported as `null`.
 */
export const resolveFocusTeamName = cache(
  async (): Promise<string | null> => {
    try {
      const team = await resolveFocusTeam();
      return team === null ? null : team.name;
    } catch {
      return null;
    }
  },
);
