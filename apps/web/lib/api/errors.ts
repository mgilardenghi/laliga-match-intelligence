/**
 * The error thrown when the API cannot answer.
 *
 * This module deliberately imports nothing, so it is safe to import from a
 * client component: the class carries no server dependency, and a boundary that
 * wants to inspect it never has to pull the data layer into the browser bundle.
 */
export class ApiUnavailableError extends Error {
  /** The HTTP status of the failed answer, or `null` when no response arrived. */
  readonly status: number | null;

  constructor(
    message: string,
    options?: { cause?: unknown; status?: number | null },
  ) {
    super(message, options);
    this.name = "ApiUnavailableError";
    this.status = options?.status ?? null;
  }
}
