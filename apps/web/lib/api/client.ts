import { cache } from "react";
import { createApiClient, type components, type paths } from "@lmi/api-client";

import { ApiUnavailableError } from "./errors";

/** An RFC 9457 problem details body, as the API declares it. */
export type Problem = components["schemas"]["Problem"];

/**
 * The outcome of one API read.
 *
 * `ok: true` carries the parsed success body. `ok: false` carries the status and
 * the problem details body when the service answered with one; `problem` is
 * `null` for any other error body, so a page can show `title` and `detail`
 * without ever rendering the raw response.
 */
export type ApiResult<T> =
  | { readonly ok: true; readonly data: T }
  | {
      readonly ok: false;
      readonly status: number;
      readonly problem: Problem | null;
    };

/** Timeout of one data request, in milliseconds. */
export const API_TIMEOUT_MS = 5000;

/**
 * Return the API base URL read from `API_URL`, defaulting to the local service.
 *
 * The value has no trailing slash, so a path can be appended directly; the
 * fallback is the same URL `pnpm dev` uses outside Compose.
 */
export function apiBaseUrl(): string {
  const baseUrl = process.env.API_URL ?? "http://localhost:8000";
  return baseUrl.replace(/\/+$/, "");
}

/**
 * The API client of the current request.
 *
 * React `cache` builds one client per request instead of one per call site.
 */
export const api = cache(() => createApiClient(apiBaseUrl()));

/** Every contract path whose entry declares a `get` operation. */
export type GetPath = {
  [P in keyof paths]: paths[P] extends { get: unknown } ? P : never;
}[keyof paths];

// `Record<never, never>` is an empty object type; it is spelled this way
// because the linter rejects the bare `{}` alias.
type RequiredKeys<T> = {
  [K in keyof T]-?: Record<never, never> extends Pick<T, K> ? never : K;
}[keyof T];

type GetOperation<P extends GetPath> = paths[P] extends {
  get: infer Operation;
}
  ? Operation
  : never;

/** The operation's whole `parameters` object: query, header, path and cookie. */
export type GetParams<P extends GetPath> =
  GetOperation<P> extends { parameters: infer Params } ? Params : never;

/** The body of the operation's `200 application/json` response. */
export type GetData<P extends GetPath> =
  GetOperation<P> extends {
    responses: {
      200: { content: { "application/json": infer Body } };
    };
  }
    ? Body
    : never;

/**
 * The arguments of `get` for one path.
 *
 * The parameters object is required exactly when the operation declares a
 * required parameter, mirroring openapi-fetch's own rule: omitting a required
 * path parameter is a compile error, while a route with no required parameter
 * needs no second argument.
 */
export type GetArgs<P extends GetPath> = RequiredKeys<
  GetParams<P>
> extends never
  ? [path: P, params?: GetParams<P>]
  : [path: P, params: GetParams<P>];

// The part of openapi-fetch's generic `GET` signature this module relies on.
// Its own signature is conditional, so TypeScript cannot resolve it through the
// generic `P` of `get`; the forwarding call below restates the same contract.
type ApiGet = <P extends GetPath>(
  path: P,
  init: {
    params?: GetParams<P>;
    cache?: RequestCache;
    signal?: AbortSignal;
  },
) => Promise<{
  data?: GetData<P>;
  error?: unknown;
  response: Response;
}>;

function isProblem(value: unknown): value is Problem {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  if (!("title" in value) || typeof value.title !== "string") {
    return false;
  }
  if (!("status" in value) || typeof value.status !== "number") {
    return false;
  }
  return Number.isInteger(value.status);
}

/**
 * Read one contract path and return its typed body or its problem details.
 *
 * A network failure or timeout, and every response with a status of 500 or
 * above, throw `ApiUnavailableError`: the API could not answer the question,
 * which is not the same as an answer of "not found". Every other non-2xx
 * returns `ok: false` with the status and the body only when it is a real RFC
 * 9457 problem; a success returns `ok: true` with the typed body.
 */
export async function get<P extends GetPath>(
  ...args: GetArgs<P>
): Promise<ApiResult<GetData<P>>> {
  const [path, params] = args;

  try {
    // `GetArgs<P>` has already checked, at every call site, that `path` and
    // `params` are this operation's own path and parameters. The cast restates
    // openapi-fetch's conditional `GET` signature, which TypeScript cannot
    // resolve through the generic `P` inside this forwarding function.
    const result = await (api().GET as ApiGet)(path, {
      params,
      cache: "no-store",
      signal: AbortSignal.timeout(API_TIMEOUT_MS),
    });

    if (result.response.status >= 500) {
      throw new ApiUnavailableError("The API failed to answer.", {
        cause: result.error,
        status: result.response.status,
      });
    }

    if (!result.response.ok) {
      return {
        ok: false,
        status: result.response.status,
        problem: isProblem(result.error) ? result.error : null,
      };
    }

    if (result.data === undefined) {
      throw new ApiUnavailableError("The API returned an empty body.", {
        status: result.response.status,
      });
    }

    return { ok: true, data: result.data };
  } catch (cause) {
    if (cause instanceof ApiUnavailableError) {
      throw cause;
    }
    throw new ApiUnavailableError("The API could not be reached.", { cause });
  }
}
