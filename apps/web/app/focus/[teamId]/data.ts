/**
 * The reads and the types the focus page and its streamed sections share.
 *
 * Two sections ask the API the same question -- one club's season statistics --
 * so that loader lives here and is wrapped in React `cache` once: the sections
 * may start their render independently, and the request is still issued once
 * per page. The same rule covers the relegation race, which both the race
 * section and the next-match panel read.
 */
import { cache } from "react";

import { get, type ApiResult, type GetData } from "@/lib/api";

/** One club's focus view of one season, as `/v1/teams/{team_id}/focus` serves it. */
export type TeamFocus = GetData<"/v1/teams/{team_id}/focus">;

/** One club's season statistics, as `/v1/teams/{team_id}/season-stats` serves it. */
export type TeamSeasonStats = GetData<"/v1/teams/{team_id}/season-stats">;

/** One club's multi-season history, as `/v1/teams/{team_id}/history` serves it. */
export type TeamHistory = GetData<"/v1/teams/{team_id}/history">;

/** One club's relegation race of one season, as the API serves it. */
export type RelegationRace = GetData<"/v1/teams/{team_id}/relegation-race">;

/** The latest succeeded simulation of one season, with its projected table. */
export type LatestSimulation = GetData<"/v1/simulations/latest">;

/** One venue split or tier record: the counters a group of matches earned. */
export type SeasonRecord = TeamSeasonStats["splits"]["home"];

/**
 * Read one club's season statistics, once per request.
 *
 * The season is passed only when the page resolved one, so the URL's own query
 * value is never forwarded as a request the API did not validate. A 404 means
 * the club has no season statistics for that season; a network failure or a 5xx
 * still throws `ApiUnavailableError` and reaches the error boundary.
 */
export const loadSeasonStats = cache(
  async (
    teamId: number,
    season: string | null,
  ): Promise<ApiResult<TeamSeasonStats>> =>
    season === null
      ? get("/v1/teams/{team_id}/season-stats", {
          path: { team_id: teamId },
        })
      : get("/v1/teams/{team_id}/season-stats", {
          path: { team_id: teamId },
          query: { season },
        }),
);

/**
 * Read one club's multi-season history, once per request.
 *
 * The endpoint takes no season: a club's history is every season it played, so
 * the answer is the whole series or an empty one for a club that played no
 * match at all.
 */
export const loadHistory = cache(
  async (teamId: number): Promise<ApiResult<TeamHistory>> =>
    get("/v1/teams/{team_id}/history", { path: { team_id: teamId } }),
);

/**
 * Read one club's relegation race of one season, once per request.
 *
 * The season is passed only when the page resolved one, for the same reason as
 * the season statistics: an unvalidated query value is never forwarded.
 */
export const loadRace = cache(
  async (
    teamId: number,
    season: string | null,
  ): Promise<ApiResult<RelegationRace>> =>
    season === null
      ? get("/v1/teams/{team_id}/relegation-race", {
          path: { team_id: teamId },
        })
      : get("/v1/teams/{team_id}/relegation-race", {
          path: { team_id: teamId },
          query: { season },
        }),
);

/**
 * Read the latest succeeded simulation of one season, once per request.
 *
 * A season whose runs all failed answers 404, which is a state of the season
 * and not an error: the caller renders it as an empty state.
 */
export const loadLatestSimulation = cache(
  async (season: string | null): Promise<ApiResult<LatestSimulation>> =>
    season === null
      ? get("/v1/simulations/latest")
      : get("/v1/simulations/latest", { query: { season } }),
);
