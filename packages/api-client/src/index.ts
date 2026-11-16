/**
 * Typed client for the LMI API.
 *
 * The types come from `schema.d.ts`, which `make api-client` generates from the
 * committed OpenAPI snapshot. A contract change therefore surfaces as a type
 * error in the dashboard instead of as a runtime surprise.
 */
// `createClient` is the default export of openapi-fetch; `Client` is a named type.
import createClient, { type Client } from "openapi-fetch";
import type { components, paths } from "./schema";

export type { components, paths };

/** The LMI API client, typed with the paths and components of the contract. */
export type ApiClient = Client<paths>;

/**
 * Build a client for `baseUrl`.
 *
 * `baseUrl` is the service root, for example `http://localhost:8000`; the `/v1`
 * prefix is part of every path in the contract, so it is not added here.
 */
export function createApiClient(baseUrl: string): ApiClient {
  return createClient<paths>({ baseUrl });
}
