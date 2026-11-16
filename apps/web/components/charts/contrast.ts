/**
 * WCAG contrast helpers for chart labels drawn on a filled shape.
 *
 * A chart fill is declared as a token with a hex fallback. The fallback is the
 * value the browser actually paints before the token resolves, so the label ink
 * is derived from the same fallback: fill and ink stay coupled to one
 * declaration and cannot disagree about which colour is behind the text.
 */

/** The light label ink used on dark fills. */
export const LIGHT_INK = "#ffffff";

/** The dark label ink used on light fills. */
export const DARK_INK = "#111827";

function parseHex(hex: string): readonly [number, number, number] {
  const match = /^#?([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(hex.trim());
  if (match === null) {
    throw new Error(`Expected a three- or six-digit hex colour, but received ${hex}`);
  }
  const digits = match[1];
  const full =
    digits.length === 3
      ? digits
          .split("")
          .map((digit) => `${digit}${digit}`)
          .join("")
      : digits;
  return [
    Number.parseInt(full.slice(0, 2), 16) / 255,
    Number.parseInt(full.slice(2, 4), 16) / 255,
    Number.parseInt(full.slice(4, 6), 16) / 255,
  ] as const;
}

function linearChannel(channel: number): number {
  return channel <= 0.03928 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
}

/** WCAG 2.1 relative luminance of an sRGB hex colour. */
export function relativeLuminance(hex: string): number {
  const [red, green, blue] = parseHex(hex);
  return (
    0.2126 * linearChannel(red) +
    0.7152 * linearChannel(green) +
    0.0722 * linearChannel(blue)
  );
}

/** WCAG 2.1 contrast ratio between two sRGB hex colours, from 1 to 21. */
export function contrastRatio(first: string, second: string): number {
  const firstLuminance = relativeLuminance(first);
  const secondLuminance = relativeLuminance(second);
  const lighter = Math.max(firstLuminance, secondLuminance);
  const darker = Math.min(firstLuminance, secondLuminance);
  return (lighter + 0.05) / (darker + 0.05);
}

/**
 * Return the ink with the higher contrast against `fill`.
 *
 * The two inks are the only label colours in the charts. A tie goes to the dark
 * ink: it is the default cell label, so a fill that does not clearly need light
 * text keeps the darker, more conservative ink.
 */
export function inkForFill(fill: string): string {
  return contrastRatio(fill, LIGHT_INK) > contrastRatio(fill, DARK_INK)
    ? LIGHT_INK
    : DARK_INK;
}
