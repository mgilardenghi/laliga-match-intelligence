export type LinearScale = {
  readonly domain: readonly [number, number];
  readonly range: readonly [number, number];
  scale(value: number): number;
  ticks(count: number): number[];
};

function niceStep(span: number, count: number): number {
  const rawStep = span / Math.max(1, count);
  const magnitude = 10 ** Math.floor(Math.log10(rawStep));
  const residual = rawStep / magnitude;
  const factor = residual <= 1 ? 1 : residual <= 2 ? 2 : residual <= 5 ? 5 : 10;
  return factor * magnitude;
}

export function linearScale(
  domain: readonly [number, number],
  range: readonly [number, number],
): LinearScale {
  const [domainMin, domainMax] = domain;
  const [rangeMin, rangeMax] = range;
  const span = domainMax - domainMin;

  return {
    domain,
    range,
    scale(value: number): number {
      if (span === 0) {
        return (rangeMin + rangeMax) / 2;
      }
      return rangeMin + ((value - domainMin) / span) * (rangeMax - rangeMin);
    },
    ticks(count: number): number[] {
      if (count <= 0 || span === 0) {
        return [domainMin];
      }
      const step = niceStep(span, count);
      const start = Math.ceil(domainMin / step) * step;
      const end = Math.floor(domainMax / step) * step;
      const values: number[] = [];
      for (let value = start; value <= end + step * 1e-9; value += step) {
        values.push(Number(value.toFixed(10)));
      }
      return values.length > 0 ? values : [domainMin, domainMax];
    },
  };
}

export type BandScale = {
  readonly count: number;
  readonly step: number;
  readonly bandwidth: number;
  start(index: number): number;
  center(index: number): number;
};

export function bandScale(
  count: number,
  range: readonly [number, number],
  paddingRatio = 0.2,
): BandScale {
  const safeCount = Math.max(0, Math.floor(count));
  const [rangeMin, rangeMax] = range;
  const span = rangeMax - rangeMin;
  const step = safeCount > 0 ? span / safeCount : span;
  const padding = Math.min(0.9, Math.max(0, paddingRatio));
  const bandwidth = step * (1 - padding);

  return {
    count: safeCount,
    step,
    bandwidth,
    start(index: number): number {
      return rangeMin + index * step + (step - bandwidth) / 2;
    },
    center(index: number): number {
      return rangeMin + (index + 0.5) * step;
    },
  };
}

export function extent(values: readonly number[]): readonly [number, number] {
  if (values.length === 0) {
    return [0, 0];
  }
  let min = values[0];
  let max = values[0];
  for (const value of values) {
    if (value < min) {
      min = value;
    }
    if (value > max) {
      max = value;
    }
  }
  return [min, max];
}

export function niceMax(value: number): number {
  if (!Number.isFinite(value) || value <= 0) {
    return 1;
  }
  const magnitude = 10 ** Math.floor(Math.log10(value));
  const residual = value / magnitude;
  const factor = residual <= 1 ? 1 : residual <= 2 ? 2 : residual <= 5 ? 5 : 10;
  return Number((factor * magnitude).toPrecision(12));
}
