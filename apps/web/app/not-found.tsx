/**
 * The page a stale or unknown link lands on.
 *
 * It renders inside the root layout, so it owns only its heading, one honest
 * sentence and a way back: a second `html`, `body` or `main` element here would
 * duplicate what the layout already sent, and the shell's navigation is still
 * on screen to leave by.
 */
import Link from "next/link";
import type { ReactElement } from "react";

import styles from "@/components/states/states.module.css";

export default function NotFound(): ReactElement {
  return (
    <div className={styles.boundary}>
      <h1 className={styles.boundaryTitle}>Page not found</h1>
      <p className={styles.boundaryText}>
        The link may be stale, or the resource it names is unknown to this
        dashboard.
      </p>
      <div className={styles.boundaryActions}>
        <Link href="/">Back to the dashboard</Link>
      </div>
    </div>
  );
}
