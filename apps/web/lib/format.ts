/** The placeholder for a value the API does not know. */
export const EM_DASH = "—";

// The chart formatters in `components/charts` own the same three-line rounding
// rule, but they are chart-local: a page must not pull a chart module into its
// bundle just to print a number, so this copy is intentional.
function fixed(value: number, digits: number): string {
  const rounded = Number(value.toFixed(digits));
  // Avoid "-0.00", which reads as a real negative value.
  return (Object.is(rounded, -0) ? 0 : rounded).toFixed(digits);
}

/**
 * Return `value` with `digits` decimals, or `EM_DASH` for an unknown value.
 *
 * `null`, `undefined` and non-finite numbers are unknown and render as an em
 * dash, never as `0`.
 */
export function formatValue(
  value: number | null | undefined,
  digits = 2,
): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return EM_DASH;
  }
  return fixed(value, digits);
}

/**
 * Return a fraction as a percentage with `digits` decimals, or `EM_DASH`.
 *
 * The input is a fraction (`0.42` renders as `42.0%`); an unknown value renders
 * as an em dash rather than `0%`.
 */
export function formatPercent(
  value: number | null | undefined,
  digits = 1,
): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return EM_DASH;
  }
  return `${fixed(value * 100, digits)}%`;
}

/**
 * Return a points total as an integer or one decimal, or `EM_DASH`.
 *
 * An unknown total renders as an em dash, never as `0`.
 */
export function formatPoints(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return EM_DASH;
  }
  const rounded = Math.round(value * 10) / 10;
  return Number.isInteger(rounded)
    ? String(rounded)
    : (Object.is(rounded, -0) ? 0 : rounded).toFixed(1);
}

/**
 * Return a count as an integer, or `EM_DASH`.
 *
 * An unknown count renders as an em dash, never as `0`.
 */
export function formatCount(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return EM_DASH;
  }
  return String(Math.round(value));
}

/**
 * Return a signed number with a leading `+` for positives, or `EM_DASH`.
 *
 * Positive values carry `+`, negative values carry their own `-`, zero has no
 * sign, and an unknown value renders as an em dash.
 */
export function formatSigned(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return EM_DASH;
  }
  const rounded = Math.round(value * 10) / 10;
  const normalized = Object.is(rounded, -0) ? 0 : rounded;
  const text = Number.isInteger(normalized)
    ? String(normalized)
    : normalized.toFixed(1);
  return normalized > 0 ? `+${text}` : text;
}
