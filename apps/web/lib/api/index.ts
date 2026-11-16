/**
 * The dashboard's public data layer.
 *
 * Pages import `get` and its types from here; the browser never calls the API.
 */
export { get, api, apiBaseUrl, API_TIMEOUT_MS } from "./client";
export type { ApiResult, Problem, GetPath, GetParams, GetData } from "./client";
export { ApiUnavailableError } from "./errors";
export { getApiStatus } from "./health";
