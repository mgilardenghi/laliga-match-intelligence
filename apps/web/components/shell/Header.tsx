import Link from "next/link";
import type { ReactElement } from "react";

import { Nav } from "./Nav";
import { ThemeToggle } from "./ThemeToggle";
import styles from "./shell.module.css";

/**
 * The banner of every page: the site name, the navigation and the theme toggle.
 *
 * The site name is a link and not an `h1`, because every page owns its own
 * heading; two `h1` elements on one page would make the outline disagree with
 * the page the reader is on.
 */
export function Header(): ReactElement {
  return (
    <header className={styles.header}>
      <div className={styles.topBar}>
        <Link className={styles.siteName} href="/">
          LaLiga Match Intelligence
        </Link>
        <ThemeToggle />
        <Nav />
      </div>
    </header>
  );
}
