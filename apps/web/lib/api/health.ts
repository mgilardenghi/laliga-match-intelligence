import { apiBaseUrl } from "./client";

/**
 * Timeout of the footer's health probe, in milliseconds.
 *
 * It is shorter than `API_TIMEOUT_MS` because the footer must answer quickly:
 * a status line is decoration, and waiting five seconds for it would hold the
 * page back longer than reading any real data.
 */
const HEALTH_TIMEOUT_MS = 2000;

function isHealthy(value: unknown): boolean {
  return (
    typeof value === "object" &&
    value !== null &&
    "status" in value &&
    value.status === "ok"
  );
}

/**
 * Return `"online"` only when `GET /health` answers `200` with `{"status":"ok"}`.
 *
 * Every other outcome, including a non-200 status, a malformed body, a network
 * failure and the 2 s timeout, returns `"offline"`; this probe never throws.
 */
export async function getApiStatus(): Promise<"online" | "offline"> {
  try {
    const response = await fetch(`${apiBaseUrl()}/health`, {
      cache: "no-store",
      signal: AbortSignal.timeout(HEALTH_TIMEOUT_MS),
    });
    if (response.status !== 200) {
      return "offline";
    }
    const body: unknown = await response.json();
    return isHealthy(body) ? "online" : "offline";
  } catch {
    return "offline";
  }
}
