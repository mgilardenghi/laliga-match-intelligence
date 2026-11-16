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

/** The origin every submitted path is resolved against; it is never visited. */
const BASE_ORIGIN = "http://dashboard.invalid";

/**
 * The local path to return to, or `null` when the submitted value is not one.
 *
 * A leading-slash test is not a same-origin test. Browsers normalise `\` to `/`
 * in the authority position, so `/\evil.example` leaves the dashboard; and a
 * `Location` that begins `//` is read as a network-path reference, so
 * `//dashboard.invalid//evil.example` resolves to this origin and still ends up
 * at `//evil.example`. The value is therefore resolved against a fixed origin,
 * the resolved origin must be that same origin, and the string that is finally
 * handed to `redirect()` is resolved once more — the browser parses it again, so
 * the check has to hold for what the browser will read, not for the input.
 *
 * A value that cannot be parsed at all (`//[`) is not a local path either, and
 * falls back like any other rejected value instead of failing the action.
 */
function localPath(value: string): string | null {
  if (!value.startsWith("/")) {
    return null;
  }

  let submitted: URL;
  try {
    submitted = new URL(value, BASE_ORIGIN);
  } catch {
    return null;
  }
  if (submitted.origin !== BASE_ORIGIN) {
    return null;
  }

  const path = `${submitted.pathname}${submitted.search}`;
  // The pathname of a same-origin URL can still start with `//`, which is exactly
  // the shape the browser reads as another host.
  if (!path.startsWith("/") || path.startsWith("//")) {
    return null;
  }

  try {
    if (new URL(path, BASE_ORIGIN).origin !== BASE_ORIGIN) {
      return null;
    }
  } catch {
    return null;
  }
  return path;
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
