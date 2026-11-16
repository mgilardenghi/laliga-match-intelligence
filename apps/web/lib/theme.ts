/**
 * The theme choice and the cookie that stores it.
 *
 * The layout reads the cookie on the server so the first paint already carries
 * the right `data-theme`, and the toggle writes it in the browser. Both sides
 * therefore share one name and one parser. The module stays import-free on
 * purpose: a client component uses it without pulling in anything server-only.
 */

/** The cookie that stores the reader's explicit theme choice. */
export const THEME_COOKIE = "lmi_theme";

/** A theme the reader picked, as opposed to following the system. */
export type ThemeChoice = "light" | "dark";

/**
 * Return the stored theme choice, or `null` when the theme follows the system.
 *
 * Only the two exact values count. A missing, empty or unexpected value means
 * "no choice", which is the safe default: the system preference is always
 * available, while a half-correct value would select a theme nobody asked for.
 */
export function parseTheme(value: string | undefined): ThemeChoice | null {
  return value === "light" || value === "dark" ? value : null;
}
