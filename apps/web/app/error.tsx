/**
 * The screen every failed page render ends on.
 *
 * Every page here renders data it read from the API, so a thrown page error
 * means the stack could not answer. The heading is therefore fixed: a built app
 * strips a server error's class and most of its message, so a heading derived
 * from the error identity would read as something else exactly where it
 * matters. For the same reason the screen does not claim to know whether the
 * failure was an availability failure: the check that could tell answers "no"
 * once a production build has stripped the class, and a wrong sentence on the
 * screen that exists for the failure is worse than none. The digest is the one
 * fact the client still has, and it ties the screen to the server log.
 */
"use client";

import Link from "next/link";
import type { ReactElement } from "react";

import styles from "@/components/states/states.module.css";

export type ErrorBoundaryProps = {
  readonly error: Error & { readonly digest?: string };
  readonly reset: () => void;
};

export default function ErrorBoundary(
  props: ErrorBoundaryProps,
): ReactElement {
  const { error, reset } = props;

  return (
    <div className={styles.boundary}>
      <h1 className={styles.boundaryTitle}>API unreachable</h1>
      <p className={styles.boundaryText}>
        Check that the stack is running, then try the request again.
      </p>
      <div className={styles.boundaryActions}>
        <button
          className={styles.button}
          onClick={() => reset()}
          type="button"
        >
          Try again
        </button>
        <Link href="/">Back to the dashboard</Link>
      </div>
      {error.digest ? (
        <p className={`${styles.muted} ${styles.mono}`}>
          Digest {error.digest}
        </p>
      ) : null}
    </div>
  );
}
