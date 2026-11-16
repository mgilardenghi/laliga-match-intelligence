"use server";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { FOCUS_COOKIE } from "./focus";

const FOCUS_COOKIE_MAX_AGE = 60 * 60 * 24 * 365;

function positiveInteger(value: string): number | null {
  if (!/^\d+$/.test(value)) {
    return null;
  }
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : null;
}

function isLocalPath(value: string): boolean {
  return value.startsWith("/") && !value.startsWith("//");
}

/**
 * Store the submitted team as the focus team and redirect to it.
 *
 * A `teamId` that is not a positive integer returns without writing anything.
 * The cookie is not a secret: it holds a public team identifier. The redirect
 * goes to `redirectTo` when that is a local path, else to `/focus/<teamId>`.
 */
export async function setFocusTeam(formData: FormData): Promise<void> {
  const rawTeamId = formData.get("teamId");
  const teamId =
    typeof rawTeamId === "string" ? positiveInteger(rawTeamId.trim()) : null;
  if (teamId === null) {
    return;
  }

  const cookieStore = await cookies();
  cookieStore.set(FOCUS_COOKIE, String(teamId), {
    httpOnly: true,
    sameSite: "lax",
    path: "/",
    maxAge: FOCUS_COOKIE_MAX_AGE,
  });

  const rawRedirectTo = formData.get("redirectTo");
  const redirectTo =
    typeof rawRedirectTo === "string" && isLocalPath(rawRedirectTo)
      ? rawRedirectTo
      : `/focus/${teamId}`;
  redirect(redirectTo);
}
