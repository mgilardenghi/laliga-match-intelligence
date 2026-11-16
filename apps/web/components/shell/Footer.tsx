import type { ReactElement } from "react";

import { get, getApiStatus } from "@/lib/api";
import { formatMadridDateTime } from "@/lib/madrid";

import styles from "./shell.module.css";

/** The three answers the freshness line can give. */
type Freshness =
  | { readonly kind: "unavailable" }
  | { readonly kind: "empty" }
  | {
      readonly kind: "loaded";
      readonly at: string;
      readonly source: string;
      readonly season: string;
    };

/**
 * Read the latest succeeded ingestion run and describe it, or say why not.
 *
 * A footer is decoration: it must never be the reason a page fails. So an
 * unreachable API, a non-2xx answer and a malformed body all become
 * `unavailable` instead of an error, and an empty page becomes its own sentence
 * rather than a date that does not exist.
 */
async function readFreshness(): Promise<Freshness> {
  try {
    const result = await get("/v1/ingestion-runs", {
      query: { status: "succeeded", limit: 1 },
    });
    if (!result.ok) {
      return { kind: "unavailable" };
    }
    const run = result.data.items[0];
    if (run === undefined) {
      return { kind: "empty" };
    }
    return {
      kind: "loaded",
      // A succeeded run normally has a finish instant; the start instant is the
      // honest fallback for a row that recorded only one of the two.
      at: run.finished_at ?? run.started_at,
      source: run.source,
      season: run.season_code,
    };
  } catch {
    return { kind: "unavailable" };
  }
}

/**
 * The footer of every page: API status, data freshness and attribution.
 *
 * The dashboard reads the local API and nothing else, and every number it shows
 * was ingested from one of the two sources named here, so the credits belong on
 * every page rather than in a documentation page nobody opens.
 */
export async function Footer(): Promise<ReactElement> {
  const [status, freshness] = await Promise.all([
    getApiStatus(),
    readFreshness(),
  ]);

  return (
    <footer className={styles.footer}>
      <div className={styles.footerInner}>
        <div className={styles.footerFacts}>
          <p className={styles.footerFact}>
            API status:{" "}
            <span
              className={
                status === "online" ? styles.statusOnline : styles.statusOffline
              }
            >
              {status}
            </span>
          </p>
          {freshness.kind === "unavailable" ? (
            <p className={styles.footerFact}>Data freshness unavailable</p>
          ) : freshness.kind === "empty" ? (
            <p className={styles.footerFact}>
              No successful ingestion recorded yet
            </p>
          ) : (
            <p className={styles.footerFact}>
              Data updated{" "}
              <time dateTime={freshness.at}>
                {formatMadridDateTime(freshness.at)}
              </time>{" "}
              from {freshness.source} for season {freshness.season}
            </p>
          )}
        </div>
        <p className={styles.attribution}>
          Results, statistics and odds come from{" "}
          <a href="https://www.football-data.co.uk/" rel="noreferrer noopener">
            football-data.co.uk
          </a>
          ; fixtures come from{" "}
          <a
            href="https://github.com/openfootball/football.json"
            rel="noreferrer noopener"
          >
            openfootball/football.json
          </a>{" "}
          (CC0). The dashboard reads only the local API.
        </p>
      </div>
    </footer>
  );
}
