/**
 * The backtest vocabulary of the performance page.
 *
 * The API serves one report of a stored walk-forward run: every model, its
 * metrics per season and per subset, and its calibration bins. This module
 * turns that payload into the few shapes the page draws -- one overall row per
 * model, the season list, the axis domain and the verdict sentence -- so the
 * page itself holds layout only and every rule about the numbers lives in one
 * readable place.
 *
 * Everything here is pure: no fetch, no React, no global state.
 */
import type { GetData } from "@/lib/api";
import { formatCount, formatValue } from "@/lib/format";
import { modelLabel, type ModelName } from "@/lib/labels";
import { firstValue, type MetricsSubset } from "@/lib/params";

export type BacktestData = GetData<"/v1/model-metrics">;
export type MetricRow = BacktestData["metrics"][number];
export type ModelInfo = BacktestData["models"][number];

/** The four scores a backtest row carries. */
export type ScoreKey = "rps" | "log_loss" | "brier" | "accuracy";

/** One model of the run and its overall row for one subset. */
export type OverallRow = {
  readonly model: ModelInfo;
  readonly metric: MetricRow;
};

export const SUBSET_LABELS: Readonly<Record<MetricsSubset, string>> = {
  all: "All forecasts",
  common: "Common matches",
};

const SUBSET_PATHS = {
  all: "/performance",
  common: "/performance?subset=common",
} as const satisfies Record<MetricsSubset, string>;

// The role of a model in the run. `lib/labels.ts` holds the model-name
// vocabulary but no kind table, and `lib/` is not this page's to edit, so the
// kind labels live next to the rows that read them.
const KIND_LABELS = {
  baseline: "Baseline",
  market: "Market",
  model: "Model",
} as const satisfies Record<string, string>;

// The longer line under a model name: what the model is, not what it scores.
const KIND_LINES = {
  baseline: "Baseline",
  market: "Benchmark: pre-closing odds, margin removed",
  model: "Fitted model",
} as const satisfies Record<string, string>;

// The calibration models the contract lists, so a URL value that names none of
// them is dropped instead of drawing a panel nobody can fill.
const CALIBRATION_MODELS = [
  "home_win",
  "base_rate",
  "market",
  "elo",
  "dixon_coles",
] as const satisfies readonly ModelName[];

/**
 * The smallest sample the verdict ranks models on: one season of ten fixtures
 * per model, the count the page treats as "more than a single matchday".
 */
const MIN_RANKING_MATCHES = 380;

/** The gap below which two scores are called level. */
const LEVEL_DELTA = 0.002;

/** The one fragment a calibration link carries. */
const CALIBRATION_FRAGMENT = "#calibration";

/**
 * Return one label of a table, or the raw value when the table has none.
 *
 * `hasOwnProperty` rather than a plain read, so a value such as `constructor`
 * cannot resolve to a member of `Object.prototype`.
 */
function lookup(table: Readonly<Record<string, string>>, value: string): string {
  return Object.prototype.hasOwnProperty.call(table, value) ? table[value] : value;
}

/** Return the label of a subset. */
export function subsetLabel(subset: MetricsSubset): string {
  return SUBSET_LABELS[subset];
}

/** Return the role label of a model, or the raw role when it is unknown. */
export function kindLabel(kind: string): string {
  return lookup(KIND_LABELS, kind);
}

/** Return the one-line description of a model role, or the raw role. */
export function kindLine(kind: string): string {
  return lookup(KIND_LINES, kind);
}

/**
 * The overall rows of one subset, one per model, in the run's model order.
 *
 * A model without such a row is left out, so the caller never prints a row of
 * em dashes for a model the run did not score. A repeated metric row for the
 * same model is ignored after the first, which keeps the report's own order the
 * authority on which row is served.
 */
export function overallRows(
  data: BacktestData,
  subset: MetricsSubset,
): readonly OverallRow[] {
  const metricByModel = new Map<string, MetricRow>();
  for (const metric of data.metrics) {
    if (
      metric.season_code === null &&
      metric.subset === subset &&
      !metricByModel.has(metric.model)
    ) {
      metricByModel.set(metric.model, metric);
    }
  }

  const rows: OverallRow[] = [];
  for (const model of data.models) {
    const metric = metricByModel.get(model.name);
    if (metric !== undefined) {
      rows.push({ model, metric });
    }
  }
  return rows;
}

/**
 * The scored seasons, ascending.
 *
 * An omitted subset reads every row, so the caller can ask which seasons the
 * run holds at all before it filters. Season codes are `2025-26`, so a plain
 * string sort is chronological.
 */
export function seasonsOf(
  data: BacktestData,
  subset?: MetricsSubset,
): readonly string[] {
  const seasons = new Set<string>();
  for (const metric of data.metrics) {
    if (metric.season_code === null) {
      continue;
    }
    if (subset !== undefined && metric.subset !== subset) {
      continue;
    }
    seasons.add(metric.season_code);
  }
  return Array.from(seasons).sort();
}

/** Return one model's RPS in one season and subset, or `null` when absent. */
export function seasonRps(
  data: BacktestData,
  subset: MetricsSubset,
  model: string,
  season: string,
): number | null {
  for (const metric of data.metrics) {
    if (
      metric.subset === subset &&
      metric.season_code === season &&
      metric.model === model
    ) {
      return metric.rps;
    }
  }
  return null;
}

/**
 * The smallest or largest finite value of one score over the rows, or `null`
 * when no row carries one.
 */
export function best(
  rows: readonly OverallRow[],
  key: ScoreKey,
  direction: "min" | "max",
): number | null {
  let result: number | null = null;
  for (const row of rows) {
    const value = row.metric[key];
    if (!Number.isFinite(value)) {
      continue;
    }
    if (
      result === null ||
      (direction === "min" ? value < result : value > result)
    ) {
      result = value;
    }
  }
  return result;
}

/**
 * Whether `value` is the best value of that score, within 1e-9.
 *
 * The tolerance is what lets a value the chart derived rather than copied --
 * an axis end, a rounded label -- still count as the best one.
 */
export function isBestValue(
  rows: readonly OverallRow[],
  key: ScoreKey,
  direction: "min" | "max",
  value: number,
): boolean {
  const extreme = best(rows, key, direction);
  return (
    Number.isFinite(value) &&
    extreme !== null &&
    Math.abs(value - extreme) <= 1e-9
  );
}

/**
 * The tightest axis domain that holds every finite value, in fiftieths.
 *
 * The axis is cut to the observed range so the gaps between models are
 * visible: a plot from zero would flatten six scores inside a tenth of itself.
 * Working in integer fiftieths keeps the two ends on a stable grid, and the
 * span is widened to at least 0.04 so a domain of one value still has a scale.
 */
export function tightDomain(values: readonly number[]): readonly [number, number] {
  const finite = values.filter((value) => Number.isFinite(value));
  if (finite.length === 0) {
    return [0, 0.04];
  }

  const lo = Math.floor(Math.min(...finite) * 50);
  let hi = Math.ceil(Math.max(...finite) * 50);
  if (hi - lo < 2) {
    hi = lo + 2;
  }
  return [lo / 50, hi / 50];
}

/**
 * The baseline rows that sit too far above the models to share their axis.
 *
 * A baseline scores far worse than a fitted model, so drawing it on the model
 * scale would flatten every model into the first fiftieth of the track. The
 * comparison is against the worst of the market and the models, and a row is
 * off scale only when it passes that by a quarter.
 */
export function offScaleRows(
  rows: readonly OverallRow[],
): readonly OverallRow[] {
  let comparisonMax: number | null = null;
  for (const row of rows) {
    if (row.model.kind === "market" || row.model.kind === "model") {
      comparisonMax =
        comparisonMax === null
          ? row.metric.rps
          : Math.max(comparisonMax, row.metric.rps);
    }
  }
  if (comparisonMax === null) {
    return [];
  }
  return rows.filter(
    (row) =>
      row.model.kind === "baseline" && row.metric.rps > 1.25 * comparisonMax,
  );
}

/**
 * The axis domain of the rows that share a scale.
 *
 * The off-scale rows are left out, unless every row is off scale: then the
 * axis still has to hold something, and the whole set is the only honest
 * choice.
 */
export function inScaleDomain(
  rows: readonly OverallRow[],
): readonly [number, number] {
  const offScale = new Set(offScaleRows(rows));
  const inScale = rows.filter((row) => !offScale.has(row));
  const plotted = inScale.length > 0 ? inScale : rows;
  return tightDomain(plotted.map((row) => row.metric.rps));
}

/** The lowest-RPS row of one kind, ties going to the earlier row. */
function lowestOfKind(
  rows: readonly OverallRow[],
  kind: string,
): OverallRow | undefined {
  let lowest: OverallRow | undefined;
  for (const row of rows) {
    if (row.model.kind !== kind) {
      continue;
    }
    if (lowest === undefined || row.metric.rps < lowest.metric.rps) {
      lowest = row;
    }
  }
  return lowest;
}

/**
 * One sentence stating what the common-match comparison says.
 *
 * The sentence is always computed on the common rows, whatever the URL
 * selects: only that subset compares the models on the same matches. When the
 * payload holds no comparison to rank -- no rows, no model row, or a sample
 * smaller than one season -- the sentence says so and ranks nothing.
 */
export function verdictText(data: BacktestData): string {
  const rows = overallRows(data, "common");
  if (rows.length === 0) {
    return "The backtest stores no common-match scores, so the page does not rank the models.";
  }

  const model = lowestOfKind(rows, "model");
  if (model === undefined) {
    return "The backtest stores no common-match scores, so the page does not rank the models.";
  }

  // The smallest count, because a comparison is only as wide as its narrowest
  // model: a sentence about matches must name the matches every model scored.
  const n = rows.reduce(
    (smallest, row) => Math.min(smallest, row.metric.n_matches),
    Number.POSITIVE_INFINITY,
  );
  if (n < MIN_RANKING_MATCHES) {
    return `Only ${formatCount(n)} matches are shared by every model, fewer than one season (${MIN_RANKING_MATCHES}), so the page does not rank the models.`;
  }

  const modelName = modelLabel(model.metric.model);
  const modelRps = formatValue(model.metric.rps, 3);
  const market = rows.find((row) => row.model.kind === "market");
  const baseline = lowestOfKind(rows, "baseline");

  let sentence: string;
  if (market !== undefined) {
    const gap = model.metric.rps - market.metric.rps;
    const marketRps = formatValue(market.metric.rps, 3);
    if (gap <= -LEVEL_DELTA) {
      sentence = `${modelName} beats the betting market on the same ${formatCount(n)} matches: RPS ${modelRps} against ${marketRps}.`;
    } else if (Math.abs(gap) < LEVEL_DELTA) {
      sentence = `${modelName} and the betting market are level on the same ${formatCount(n)} matches: RPS ${modelRps} against ${marketRps}, a gap under 0.002.`;
    } else {
      sentence = `The betting market is the best forecaster on the same ${formatCount(n)} matches: RPS ${marketRps}, ahead of the best model, ${modelName}, at ${modelRps}.`;
    }
  } else {
    sentence = `${modelName} is the best model on the same ${formatCount(n)} matches: RPS ${modelRps}.`;
  }

  if (baseline === undefined) {
    return sentence;
  }

  const baselineName = modelLabel(baseline.metric.model);
  const baselineRps = formatValue(baseline.metric.rps, 3);
  if (model.metric.rps <= baseline.metric.rps - LEVEL_DELTA) {
    const gap = formatValue(Math.abs(model.metric.rps - baseline.metric.rps), 3);
    return `${sentence} ${modelName} beats the best baseline, ${baselineName} (${baselineRps}), by ${gap}.`;
  }
  return `${sentence} ${modelName} does not beat the best baseline, ${baselineName} (${baselineRps}), by the 0.002 margin.`;
}

/**
 * The URL of one subset, optionally keeping the drawn calibration model.
 *
 * The subset comes first when it is not the default, so a shared link reads as
 * a subset of the page rather than as a calibration with an argument.
 */
export function subsetHref(
  subset: MetricsSubset,
  calibration: string | null,
): string {
  const base = SUBSET_PATHS[subset];
  if (calibration === null) {
    return base;
  }
  const separator = subset === "common" ? "&" : "?";
  return `${base}${separator}calibration=${encodeURIComponent(calibration)}`;
}

/**
 * The URL of one drawn calibration model, with the fragment that scrolls to
 * the panel.
 */
export function calibrationHref(
  subset: MetricsSubset,
  calibration: string,
): string {
  return `${subsetHref(subset, calibration)}${CALIBRATION_FRAGMENT}`;
}

/**
 * The calibration model a URL selects, or `null`.
 *
 * A missing, empty or unknown value is no selection: the page then draws the
 * first model that holds a bin, which is the run's own order.
 */
export function parseCalibration(
  value: string | string[] | undefined,
): string | null {
  const candidate = firstValue(value)?.trim();
  if (candidate === undefined || candidate === "") {
    return null;
  }
  return (CALIBRATION_MODELS as readonly string[]).includes(candidate)
    ? candidate
    : null;
}
