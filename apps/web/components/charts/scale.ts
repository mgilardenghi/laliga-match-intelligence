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

export type FittedDomain = {
  readonly domain: readonly [number, number];
  readonly ticks: readonly number[];
};

/** Round away the float noise a division leaves, without losing real digits. */
function settle(value: number): number {
  return Number(value.toPrecision(12));
}

/** The next 1/2/5 x 10^k step above `step`. */
function nextNiceStep(step: number): number {
  const magnitude = 10 ** Math.floor(Math.log10(step));
  const residual = step / magnitude;
  if (residual < 1.5) {
    return 2 * magnitude;
  }
  if (residual < 3.5) {
    return 5 * magnitude;
  }
  return 10 * magnitude;
}

/**
 * Fit an axis to the data instead of to a round guess.
 *
 * The domain starts at zero, or at the next nice value below the minimum when
 * the data holds negative values. It ends at the smallest multiple of a
 * 1/2/5 x 10^k step that is at least the maximum and that splits the axis into
 * at most `tickCount` intervals, so the last tick is the domain maximum and the
 * axis never runs more than one step past the data.
 */
export function fittedDomain(values: readonly number[], tickCount: number): FittedDomain {
  const intervals = Math.max(1, Math.floor(tickCount));
  let minimum = 0;
  let maximum = 0;
  let finite = 0;
  for (const value of values) {
    if (!Number.isFinite(value)) {
      continue;
    }
    finite += 1;
    if (value < minimum) {
      minimum = value;
    }
    if (value > maximum) {
      maximum = value;
    }
  }

  // No finite value, or every value exactly zero: a degenerate axis would draw
  // nothing, so fall back to one readable interval.
  const start = minimum < 0 ? minimum : 0;
  if (finite === 0 || maximum - start <= 0) {
    return { domain: [0, 1], ticks: [0, 1] };
  }

  let step = niceStep(maximum - start, intervals);
  let lower = 0;
  let upper = 1;
  let count = 1;
  for (let attempt = 0; attempt < 16; attempt += 1) {
    lower = start < 0 ? Math.floor(settle(start / step)) * step : 0;
    upper = Math.ceil(settle(maximum / step)) * step;
    count = Math.round(settle((upper - lower) / step));
    if (count >= 1 && count <= intervals) {
      break;
    }
    step = nextNiceStep(step);
  }

  const domainMinimum = settle(lower);
  const domainMaximum = settle(upper);
  const ticks: number[] = [];
  for (let index = 0; index <= count; index += 1) {
    ticks.push(settle(lower + index * step));
  }
  // The last tick is the axis end by definition, even when the float sum of the
  // step lands a hair beside it.
  ticks[ticks.length - 1] = domainMaximum;
  return { domain: [domainMinimum, domainMaximum], ticks };
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
