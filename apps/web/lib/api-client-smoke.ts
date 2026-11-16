/**
 * Temporary contract check for the generated API client.
 *
 * Nothing renders this module. It exists so `tsc` type-checks one real call
 * through `@lmi/api-client` against the committed OpenAPI snapshot, which is
 * what turns a stale client or a changed response shape into a build failure
 * instead of a runtime surprise. A later M5 task adds the dashboard pages that
 * read the API and deletes this file with them.
 */
import { createApiClient, type ApiClient } from "@lmi/api-client";

/**
 * Read the season codes the API serves, or an empty list when the request
 * failed. The result is typed by the generated client, so a contract change
 * that drops `code` no longer compiles here.
 */
export async function readSeasonCodes(baseUrl: string): Promise<string[]> {
  const client: ApiClient = createApiClient(baseUrl);
  const { data, error } = await client.GET("/v1/seasons");

  if (error) {
    return [];
  }

  return (data ?? []).map((season) => season.code);
}
