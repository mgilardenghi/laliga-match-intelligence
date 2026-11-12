const DEFAULT_API_URL = "http://localhost:8000";
const REQUEST_TIMEOUT_MS = 2000;

type HealthResponse = {
  status?: unknown;
};

function isHealthy(body: unknown): boolean {
  return (
    typeof body === "object" &&
    body !== null &&
    (body as HealthResponse).status === "ok"
  );
}

/**
 * Ask the API whether it is healthy. Returns "online" only when the API
 * answers HTTP 200 with `{"status": "ok"}`; every other outcome, including a
 * timeout, is "offline".
 */
export async function getApiStatus(): Promise<"online" | "offline"> {
  const baseUrl = process.env.API_URL ?? DEFAULT_API_URL;

  try {
    const response = await fetch(`${baseUrl}/health`, {
      cache: "no-store",
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
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
