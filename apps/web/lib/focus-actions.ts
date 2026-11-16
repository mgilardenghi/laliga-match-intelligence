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

/**
 * The local path to return to, or `null` when the submitted value is not one.
 *
 * A leading-slash test is not enough: browsers normalise `\` to `/` in the
 * authority position, so `/[backslash]evil.example` and `//evil.example` both
 * leave the dashboard even though only one of them starts with `//`. The value is
 * therefore resolved against a fixed origin and kept only when the resolved
 * origin is that same origin, which checks the URL the browser will follow
 * rather than the string it was built from.
 */
function localPath(value: string): string | null {
  if (!value.startsWith("/")) {
    return null;
  }
  const origin = "http://dashboard.invalid";
  const url = new URL(value, origin);
  if (url.origin !== origin) {
    return null;
  }
  return `${url.pathname}${url.search}`;
}

/**
 * Store the submitted team as the focus team and redirect to it.
 *
 * A `teamId` that is not a positive integer returns without writing anything.
 * The cookie is not a secret: it holds a public team identifier. The redirect
 * goes to `redirectTo` when that resolves to a path on this dashboard, else to
 * `/focus/<teamId>`.
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
    (typeof rawRedirectTo === "string" ? localPath(rawRedirectTo) : null) ??
    `/focus/${teamId}`;
  redirect(redirectTo);
}
