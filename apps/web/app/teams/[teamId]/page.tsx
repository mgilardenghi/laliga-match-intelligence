/**
 * The team page route: one club's season analytics.
 *
 * The dynamic segment is user input, so it is validated here and never reaches
 * the API: a segment that is not a positive integer is an unknown page, not a
 * request the API should refuse. Everything the page renders lives in the
 * page-local module next door, which owns the reads, the sections and their
 * states; this file only turns the route's promises into that module's props.
 */
import { notFound } from "next/navigation";
import type { ReactElement } from "react";

import type { SearchParams } from "@/lib/params";

import { TeamPage } from "../_team/team-page";

export const dynamic = "force-dynamic";

type TeamRouteProps = {
  readonly params: Promise<{ readonly teamId: string }>;
  readonly searchParams: Promise<SearchParams>;
};

// A team identifier is a positive integer, and `Number("1e3")` and
// `Number(" 7 ")` are integers the API would also accept while the URL said
// something else, so the digits are checked before the value is converted.
const TEAM_ID_PATTERN = /^[1-9]\d*$/;

/** The identifier the route names, or `null` when it names no club. */
function parseTeamId(raw: string): number | null {
  if (!TEAM_ID_PATTERN.test(raw)) {
    return null;
  }
  const teamId = Number(raw);
  return Number.isSafeInteger(teamId) ? teamId : null;
}

export default async function TeamRoute(
  props: TeamRouteProps,
): Promise<ReactElement> {
  const [{ teamId }, searchParams] = await Promise.all([
    props.params,
    props.searchParams,
  ]);

  const id = parseTeamId(teamId);
  if (id === null) {
    notFound();
  }

  return <TeamPage teamId={id} params={searchParams} />;
}
