import type { ReactElement } from "react";

import styles from "./ui.module.css";

/**
 * The legend of the dashboard.
 *
 * A swatch carries no meaning on its own: every entry keeps its text label, so
 * a reader who cannot tell the colours apart still reads the legend. The swatch
 * colour comes from a token through a static class map, never from a value in
 * this component, so the legend follows the theme like the charts do.
 */
export type SemanticToken =
  | "side-home"
  | "side-draw"
  | "side-away"
  | "result-w"
  | "result-d"
  | "result-l"
  | "zone-ucl"
  | "zone-europe"
  | "zone-relegation";

export type LegendItem = {
  readonly token: SemanticToken;
  readonly label: string;
};

export type LegendProps = {
  /** The entries to show; defaults to the app legend. */
  readonly items?: readonly LegendItem[];
  /** An optional line above the list that names what the legend covers. */
  readonly caption?: string;
};

const DEFAULT_ITEMS: readonly LegendItem[] = [
  { token: "side-home", label: "Home side · the focus club" },
  { token: "side-draw", label: "Draw · league average" },
  { token: "side-away", label: "Away side · the opponent" },
  { token: "result-w", label: "Win · Champions League places" },
  { token: "result-d", label: "Draw · Europe places" },
  { token: "result-l", label: "Loss · relegation places" },
];

const SWATCH_CLASSES: Readonly<Record<SemanticToken, string>> = {
  "side-home": styles.swatchSideHome,
  "side-draw": styles.swatchSideDraw,
  "side-away": styles.swatchSideAway,
  "result-w": styles.swatchResultW,
  "result-d": styles.swatchResultD,
  "result-l": styles.swatchResultL,
  "zone-ucl": styles.swatchZoneUcl,
  "zone-europe": styles.swatchZoneEurope,
  "zone-relegation": styles.swatchZoneRelegation,
};

export function Legend(props: LegendProps): ReactElement {
  const { items = DEFAULT_ITEMS, caption } = props;

  return (
    <div className={styles.legend}>
      {caption ? <p className={styles.legendCaption}>{caption}</p> : null}
      <ul className={styles.legendList}>
        {items.map((item, index) => (
          <li key={`${item.token}-${index}`} className={styles.legendItem}>
            <span
              className={`${styles.swatch} ${SWATCH_CLASSES[item.token]}`}
              aria-hidden="true"
            />
            <span className={styles.legendLabel}>{item.label}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
