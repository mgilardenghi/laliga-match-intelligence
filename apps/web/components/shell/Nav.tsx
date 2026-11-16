import Link from "next/link";
import type { ReactElement } from "react";

import { resolveFocusTeamName } from "@/lib/focus";

import styles from "./shell.module.css";

/**
 * The primary navigation of the dashboard.
 *
 * The Focus entry names the team the focus page will open on, so a reader knows
 * what the link leads to before following it. The name comes from
 * `resolveFocusTeamName`, which never throws: a header must still render when
 * the API is down, and the link then reads `Focus` alone.
 */
export async function Nav(): Promise<ReactElement> {
  const focusTeam = await resolveFocusTeamName();

  return (
    <nav aria-label="Main" className={styles.nav}>
      <ul className={styles.navList}>
        <li>
          <Link className={styles.navLink} href="/">
            Table
          </Link>
        </li>
        <li>
          <Link className={styles.navLink} href="/matchday">
            Matchday
          </Link>
        </li>
        <li>
          <Link className={styles.navLink} href="/teams">
            Teams
          </Link>
        </li>
        <li>
          {/* The href stays `/focus` whatever the team is: the page resolves
              the team itself, and a name in the query would pin a stale one. */}
          <Link className={styles.navLink} href="/focus">
            {focusTeam === null ? "Focus" : `Focus: ${focusTeam}`}
          </Link>
        </li>
        <li>
          <Link className={styles.navLink} href="/performance">
            Performance
          </Link>
        </li>
      </ul>
    </nav>
  );
}
