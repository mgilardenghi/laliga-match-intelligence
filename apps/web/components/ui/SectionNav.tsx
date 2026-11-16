import type { ReactElement } from "react";

import styles from "./ui.module.css";

/**
 * In-page navigation to the sections of one page.
 *
 * The links are plain fragments, so they work without JavaScript and Next.js
 * keeps its document. The bar is sticky by default; the list wraps instead of
 * overflowing sideways, which keeps every section reachable on a phone.
 */
export type SectionNavItem = {
  readonly id: string;
  readonly label: string;
};

export type SectionNavProps = {
  /** The target element ids and the label each one shows. */
  readonly sections: readonly SectionNavItem[];
  /** Stick the bar to the top of the viewport; defaults to `true`. */
  readonly sticky?: boolean;
};

export function SectionNav(props: SectionNavProps): ReactElement {
  const { sections, sticky = true } = props;

  return (
    <nav
      aria-label="Sections of this page"
      className={sticky ? `${styles.sectionNav} ${styles.sectionNavSticky}` : styles.sectionNav}
    >
      <ul className={styles.sectionNavList}>
        {sections.map((section) => (
          <li key={section.id}>
            <a className={styles.sectionNavLink} href={`#${section.id}`}>
              {section.label}
            </a>
          </li>
        ))}
      </ul>
    </nav>
  );
}
