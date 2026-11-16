import type { ReactElement } from "react";

import styles from "./shell.module.css";

/**
 * The first focusable element of every page.
 *
 * Without it a keyboard user walks the whole header and the whole navigation
 * before reaching the content of the page, on every request.
 */
export function SkipLink(): ReactElement {
  return (
    <a className={styles.skipLink} href="#main">
      Skip to content
    </a>
  );
}
