function fixed(value: number, digits: number): string {
  if (!Number.isFinite(value)) {
    return "—";
  }
  // Avoid "-0.00" and "-0.0%", which read as a real negative value.
  const rounded = Number(value.toFixed(digits));
  return (Object.is(rounded, -0) ? 0 : rounded).toFixed(digits);
}

export function formatPercent(value: number, digits = 1): string {
  if (!Number.isFinite(value)) {
    return "—";
  }
  return `${fixed(value * 100, digits)}%`;
}

export function formatNumber(value: number, digits = 2): string {
  return fixed(value, digits);
}

export function formatPoints(value: number): string {
  if (!Number.isFinite(value)) {
    return "—";
  }
  const rounded = Math.round(value * 10) / 10;
  return Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(1);
}
