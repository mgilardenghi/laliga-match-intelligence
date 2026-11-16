/**
 * The placeholder a `<Suspense>` boundary shows while a section is still reading.
 *
 * It exists so the streamed page reserves the space a section will take instead
 * of jumping when the data lands, and so a reader who cannot see the screen is
 * told which section is on its way. The bars are inert and hidden from the
 * accessibility tree, and nothing animates, because the dashboard has no motion
 * by design.
 */
import type { ReactElement } from "react";

import styles from "./states.module.css";

export type SectionSkeletonProps = {
  /** The section being waited for, for example `Season table`. */
  readonly label: string;
  /** How many placeholder bars to draw. */
  readonly rows?: number;
};

const DEFAULT_ROWS = 3;

export function SectionSkeleton(props: SectionSkeletonProps): ReactElement {
  const { label, rows = DEFAULT_ROWS } = props;
  const bars = Array.from({ length: rows }, (_unused, index) => index);

  return (
    <div className={styles.skeleton} role="status">
      <p className={styles.skeletonLabel}>{`${label} loading…`}</p>
      <div aria-hidden="true" className={styles.skeletonBars}>
        {bars.map((index) => (
          <div className={styles.skeletonBar} key={index} />
        ))}
      </div>
    </div>
  );
}
