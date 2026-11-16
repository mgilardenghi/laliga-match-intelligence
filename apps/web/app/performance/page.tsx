/**
 * The performance page: the stored walk-forward backtest and the live forecast
 * report, side by side.
 *
 * Both reports come from the API and share one metric vocabulary, so the page
 * fetches them in parallel and renders each section in its own state. A section
 * whose report is missing says so and names the command that produces it,
 * instead of drawing an empty table or a zero nobody measured. The subset
 * control narrows the backtest to the matches every model scored; the live
 * endpoint reports that comparison itself, in `common`.
 */
import Link from "next/link";
import { Fragment, type ReactElement } from "react";

import {
  CalibrationChart,
  DotPlot,
  LineChart,
  type DotPlotReference,
  type LineSeries,
} from "@/components/charts";
import { EmptyState, ProblemState } from "@/components/states";
import { get, type GetData } from "@/lib/api";
import { EM_DASH, formatCount, formatPercent, formatValue } from "@/lib/format";
import { modelLabel } from "@/lib/labels";
import {
  parseSubset,
  type MetricsSubset,
  type SearchParams,
} from "@/lib/params";

import styles from "./page.module.css";

export const dynamic = "force-dynamic";

type BacktestData = GetData<"/v1/model-metrics">;
type LiveData = GetData<"/v1/model-metrics/live">;

// The backtest states each model's role, and this page is the only place that
// renders it. `lib/labels.ts` holds the model-name vocabulary but no kind
// table, and `lib/` is not this page's to edit, so the kind labels live here.
const KIND_LABELS = {
  baseline: "Baseline",
  market: "Market",
  model: "Model",
} satisfies Record<"baseline" | "market" | "model", string>;

const SUBSET_PATHS = {
  all: "/performance",
  common: "/performance?subset=common",
} as const satisfies Record<MetricsSubset, string>;

const SUBSET_LABELS = {
  all: "All forecasts",
  common: "Common matches",
} as const satisfies Record<MetricsSubset, string>;

// The two references drawn on the dot plot. Their short labels keep the plot
// legible, because a reference label is centred on its line and the longer
// `modelLabel` text would overflow the SVG at the right edge.
const DOT_PLOT_REFERENCES = [
  { name: "home_win", label: "Home-win" },
  { name: "market", label: "Market" },
] as const;

/** Return the role label of a model, or the raw role when it is unknown. */
function kindLabel(value: string): string {
  return Object.prototype.hasOwnProperty.call(KIND_LABELS, value)
    ? KIND_LABELS[value as keyof typeof KIND_LABELS]
    : value;
}

/** Return a stored string, or an em dash when the API stored none. */
function textValue(value: string | null | undefined): string {
  return value === null || value === undefined || value.trim() === ""
    ? EM_DASH
    : value;
}

/** Keep only a number the API actually answered, never a missing value. */
function finiteOrNull(value: number | null | undefined): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/**
 * The two links that choose the backtest subset.
 *
 * It is a list of links rather than a second navigation landmark: the shell
 * owns the one navigation region, and a page-level filter is not site
 * navigation. The current choice carries `aria-current`, so its state reaches
 * assistive technology as well as the eye.
 */
function SubsetControl(props: {
  readonly active: MetricsSubset;
}): ReactElement {
  const { active } = props;
  const subsets: readonly MetricsSubset[] = ["all", "common"];

  return (
    <div className={styles.subsets}>
      <ul className={styles.subsetList}>
        {subsets.map((subset) => (
          <li key={subset}>
            <Link
              className={styles.subsetLink}
              href={SUBSET_PATHS[subset]}
              aria-current={subset === active ? "page" : undefined}
            >
              {SUBSET_LABELS[subset]}
            </Link>
          </li>
        ))}
      </ul>
      <p className={styles.note}>
        <strong>Common matches</strong> restricts every model to the fixtures all
        models scored, so a comparison uses the same matches for each of them.
      </p>
    </div>
  );
}

/**
 * The backtest report: the run header, the model list, the overall metrics
 * table and the three charts.
 *
 * The metrics rows are filtered here rather than ordered by the API: the
 * payload holds them season by season, and the page needs the overall rows of
 * one subset in the model order the run states, so a table row and a model
 * entry always name the same model.
 */
function BacktestReport(props: {
  readonly data: BacktestData;
  readonly subset: MetricsSubset;
}): ReactElement {
  const { data, subset } = props;
  const otherSubset: MetricsSubset = subset === "all" ? "common" : "all";

  // One overall row per model, keyed by the model name the run lists.
  const metricsByModel = new Map<string, BacktestData["metrics"][number]>();
  for (const metric of data.metrics) {
    if (
      metric.season_code === null &&
      metric.subset === subset &&
      !metricsByModel.has(metric.model)
    ) {
      metricsByModel.set(metric.model, metric);
    }
  }
  const modelsWithMetrics = data.models.filter((model) =>
    metricsByModel.has(model.name),
  );

  // The per-season rows of the same subset, in ascending season order, and one
  // RPS lookup per model and season for the line chart.
  const seasons = Array.from(
    new Set(
      data.metrics
        .filter((metric) => metric.subset === subset)
        .map((metric) => metric.season_code)
        .filter((code): code is string => code !== null),
    ),
  ).sort();
  const rpsByModelSeason = new Map<string, number | null>();
  for (const metric of data.metrics) {
    if (metric.subset === subset && metric.season_code !== null) {
      rpsByModelSeason.set(`${metric.model}\u0000${metric.season_code}`, metric.rps);
    }
  }

  const dotRows = modelsWithMetrics.map((model) => {
    const metric = metricsByModel.get(model.name);
    return {
      model: modelLabel(model.name),
      values: [finiteOrNull(metric?.rps), finiteOrNull(metric?.log_loss)],
    };
  });

  const references: DotPlotReference[] = [];
  for (const reference of DOT_PLOT_REFERENCES) {
    const metric = metricsByModel.get(reference.name);
    const rps = finiteOrNull(metric?.rps);
    const logLoss = finiteOrNull(metric?.log_loss);
    if (rps !== null) {
      references.push({ metric: "RPS", label: reference.label, value: rps });
    }
    if (logLoss !== null) {
      references.push({
        metric: "Log loss",
        label: reference.label,
        value: logLoss,
      });
    }
  }

  const series: readonly LineSeries[] = data.models.slice(0, 5).map((model) => ({
    name: modelLabel(model.name),
    values: seasons.map(
      (season) => rpsByModelSeason.get(`${model.name}\u0000${season}`) ?? null,
    ),
  }));

  const emptyProse =
    subset === "all"
      ? "The latest succeeded run stores no overall row for the forecasts of every model. "
      : "The latest succeeded run stores no overall row for the fixtures every model scored. ";

  return (
    <>
      <dl className={styles.pairs}>
        <dt className={styles.term}>Matched scored</dt>
        <dd className={styles.value}>{formatCount(data.run.n_matches)}</dd>
        <dt className={styles.term}>From year</dt>
        <dd className={styles.value}>{formatCount(data.run.from_year)}</dd>
        <dt className={styles.term}>Step days</dt>
        <dd className={styles.value}>{formatCount(data.run.step_days)}</dd>
        <dt className={styles.term}>Seed</dt>
        <dd className={styles.value}>{formatCount(data.run.seed)}</dd>
        <dt className={styles.term}>Code version</dt>
        <dd className={styles.value}>
          <code className={styles.code}>{textValue(data.run.code_version)}</code>
        </dd>
        <dt className={styles.term}>Data fingerprint</dt>
        <dd className={styles.value}>
          <code className={styles.code}>
            {textValue(data.run.data_fingerprint)}
          </code>
        </dd>
      </dl>

      <dl className={styles.pairs} aria-label="Models in this run">
        {data.models.map((model) => (
          <Fragment key={model.name}>
            <dt className={styles.term}>{modelLabel(model.name)}</dt>
            <dd className={styles.value}>
              {kindLabel(model.kind)}, version {textValue(model.version)}
            </dd>
          </Fragment>
        ))}
      </dl>

      {modelsWithMetrics.length === 0 ? (
        <EmptyState title="No metrics for this subset">
          <p>
            {emptyProse}
            <Link href={SUBSET_PATHS[otherSubset]}>
              {SUBSET_LABELS[otherSubset]}
            </Link>{" "}
            reads the other match set.
          </p>
        </EmptyState>
      ) : (
        <>
          <div className={styles.tableWrap}>
            <table className={styles.table} id="backtest-metrics">
              <caption className={styles.caption}>
                Overall backtest metrics, {SUBSET_LABELS[subset].toLowerCase()}
              </caption>
              <thead>
                <tr>
                  <th className={`${styles.cell} ${styles.headCell}`} scope="col">
                    Model
                  </th>
                  <th className={`${styles.cell} ${styles.headCell}`} scope="col">
                    Kind
                  </th>
                  <th
                    className={`${styles.cell} ${styles.headCell} ${styles.numeric}`}
                    scope="col"
                  >
                    Log loss
                  </th>
                  <th
                    className={`${styles.cell} ${styles.headCell} ${styles.numeric}`}
                    scope="col"
                  >
                    Brier
                  </th>
                  <th
                    className={`${styles.cell} ${styles.headCell} ${styles.numeric}`}
                    scope="col"
                  >
                    RPS
                  </th>
                  <th
                    className={`${styles.cell} ${styles.headCell} ${styles.numeric}`}
                    scope="col"
                  >
                    Accuracy
                  </th>
                  <th
                    className={`${styles.cell} ${styles.headCell} ${styles.numeric}`}
                    scope="col"
                  >
                    Matches
                  </th>
                  <th
                    className={`${styles.cell} ${styles.headCell} ${styles.numeric}`}
                    scope="col"
                  >
                    Coverage
                  </th>
                </tr>
              </thead>
              <tbody>
                {modelsWithMetrics.map((model) => {
                  const metric = metricsByModel.get(model.name);
                  return (
                    <tr key={model.name}>
                      <th
                        className={`${styles.cell} ${styles.rowHeader}`}
                        scope="row"
                      >
                        {modelLabel(model.name)}
                      </th>
                      <td className={styles.cell}>{kindLabel(model.kind)}</td>
                      <td className={`${styles.cell} ${styles.numeric}`}>
                        {formatValue(metric?.log_loss, 3)}
                      </td>
                      <td className={`${styles.cell} ${styles.numeric}`}>
                        {formatValue(metric?.brier, 3)}
                      </td>
                      <td className={`${styles.cell} ${styles.numeric}`}>
                        {formatValue(metric?.rps, 3)}
                      </td>
                      <td className={`${styles.cell} ${styles.numeric}`}>
                        {formatPercent(metric?.accuracy)}
                      </td>
                      <td className={`${styles.cell} ${styles.numeric}`}>
                        {formatCount(metric?.n_matches)}
                      </td>
                      <td className={`${styles.cell} ${styles.numeric}`}>
                        {formatPercent(metric?.coverage)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className={styles.note}>
            Log loss, Brier and RPS are errors, so lower is better; accuracy is a
            hit rate, so higher is better.
          </p>
        </>
      )}

      <DotPlot
        id="backtest-dot-plot"
        title="RPS and log loss by model"
        description="Two panels, ranked probability score and log loss, with one row per model. Filled dots are each model's value; the dashed lines mark the home-win baseline and the market reference."
        metrics={["RPS", "Log loss"]}
        rows={dotRows}
        references={references}
      />

      {seasons.length > 0 ? (
        <LineChart
          id="backtest-rps-by-season"
          title="RPS by season"
          description="Ranked probability score of each model in every scored season of the backtest. A lower line is better, and a gap means the model scored no match in that season."
          yLabel="RPS"
          xLabels={seasons}
          series={series}
        />
      ) : null}

      <CalibrationChart
        id="backtest-calibration"
        title="Calibration by outcome"
        description="Mean predicted probability against observed frequency for home wins, draws and away wins, one shape per model and one point per bin. The diagonal marks perfect calibration and the dot area grows with the number of matches."
        points={data.calibration.map((bin) => ({
          model: modelLabel(bin.model),
          outcome: bin.outcome,
          meanPredicted: bin.mean_predicted,
          observedFrequency: bin.observed_frequency,
          n: bin.n,
        }))}
        note="Calibration bins carry neither a subset nor a season, so this chart is the same whichever subset is chosen above."
      />
    </>
  );
}

/** One score triple as the two report tables print it. */
function scoreCells(scores: {
  readonly log_loss: number;
  readonly brier: number;
  readonly rps: number;
  readonly accuracy: number;
}): ReactElement {
  return (
    <>
      <td className={`${styles.cell} ${styles.numeric}`}>
        {formatValue(scores.log_loss, 3)}
      </td>
      <td className={`${styles.cell} ${styles.numeric}`}>
        {formatValue(scores.brier, 3)}
      </td>
      <td className={`${styles.cell} ${styles.numeric}`}>
        {formatValue(scores.rps, 3)}
      </td>
      <td className={`${styles.cell} ${styles.numeric}`}>
        {formatPercent(scores.accuracy)}
      </td>
    </>
  );
}

/**
 * The live report: the as-of date, the prediction counts, the per-model table,
 * the common-match comparison and the market reference.
 *
 * These rows are the predictions the worker stored before kickoff, so a model
 * that skipped a fixture shows a smaller row count rather than a zero. Only the
 * scored rows carry scores; the other counts are what a reader needs to see why
 * coverage can sit below one.
 */
function LiveReport(props: { readonly data: LiveData }): ReactElement {
  const { data } = props;
  const common = data.common;
  const market = data.market_reference;

  return (
    <>
      <p className={styles.evaluated}>
        Evaluated as of{" "}
        <time dateTime={data.evaluated_as_of}>{data.evaluated_as_of}</time>
      </p>

      <dl className={styles.pairs} aria-label="Live prediction counts">
        <dt className={styles.term}>Prediction rows</dt>
        <dd className={styles.value}>{formatCount(data.counts.n_rows)}</dd>
        <dt className={styles.term}>Scored</dt>
        <dd className={styles.value}>{formatCount(data.counts.n_scored)}</dd>
        <dt className={styles.term}>Pending</dt>
        <dd className={styles.value}>{formatCount(data.counts.n_pending)}</dd>
        <dt className={styles.term}>Late</dt>
        <dd className={styles.value}>{formatCount(data.counts.n_late)}</dd>
        <dt className={styles.term}>Superseded</dt>
        <dd className={styles.value}>{formatCount(data.counts.n_superseded)}</dd>
        <dt className={styles.term}>Withdrawn</dt>
        <dd className={styles.value}>{formatCount(data.counts.n_withdrawn)}</dd>
      </dl>
      <p className={styles.note}>
        Only the scored rows carry a score; every other count is a reason the
        coverage of a model can sit below one.
      </p>

      {/* Three states, not two: a stored row and a scored row are different
          facts, so a section that holds predictions it cannot score yet says
          so instead of drawing an empty table. */}
      {data.counts.n_rows === 0 ? (
        <EmptyState title="No predictions stored yet" command="make predict">
          <p>
            The worker stores one forecast per match and model before kickoff,
            and none is stored for the season the API serves.
          </p>
        </EmptyState>
      ) : data.models.length === 0 ? (
        <EmptyState title="No scored predictions yet">
          <p>
            The worker stored {formatCount(data.counts.n_rows)} prediction rows
            for the season the API serves, but none of them is on a played
            match with an on-time forecast, so there is no score to compare
            yet.
          </p>
        </EmptyState>
      ) : (
        <>
          <div className={styles.tableWrap}>
            <table className={styles.table} id="live-metrics">
              <caption className={styles.caption}>
                Live metrics per model and match set
              </caption>
              <thead>
                <tr>
                  <th className={`${styles.cell} ${styles.headCell}`} scope="col">
                    Model
                  </th>
                  <th className={`${styles.cell} ${styles.headCell}`} scope="col">
                    Match set
                  </th>
                  <th
                    className={`${styles.cell} ${styles.headCell} ${styles.numeric}`}
                    scope="col"
                  >
                    Matches
                  </th>
                  <th
                    className={`${styles.cell} ${styles.headCell} ${styles.numeric}`}
                    scope="col"
                  >
                    Log loss
                  </th>
                  <th
                    className={`${styles.cell} ${styles.headCell} ${styles.numeric}`}
                    scope="col"
                  >
                    Brier
                  </th>
                  <th
                    className={`${styles.cell} ${styles.headCell} ${styles.numeric}`}
                    scope="col"
                  >
                    RPS
                  </th>
                  <th
                    className={`${styles.cell} ${styles.headCell} ${styles.numeric}`}
                    scope="col"
                  >
                    Accuracy
                  </th>
                </tr>
              </thead>
              <tbody>
                {data.models.map((entry) => (
                  <tr key={`own-${entry.model}`}>
                    <th
                      className={`${styles.cell} ${styles.rowHeader}`}
                      scope="row"
                    >
                      {modelLabel(entry.model)}
                    </th>
                    <td className={styles.cell}>Own rows</td>
                    <td className={`${styles.cell} ${styles.numeric}`}>
                      {formatCount(entry.scores.n_matches)}
                    </td>
                    {scoreCells(entry.scores)}
                  </tr>
                ))}
                {(common?.models ?? []).map((entry) => (
                  <tr key={`common-${entry.model}`}>
                    <th
                      className={`${styles.cell} ${styles.rowHeader}`}
                      scope="row"
                    >
                      {modelLabel(entry.model)}
                    </th>
                    <td className={styles.cell}>Common matches</td>
                    <td className={`${styles.cell} ${styles.numeric}`}>
                      {formatCount(entry.scores.n_matches)}
                    </td>
                    {scoreCells(entry.scores)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className={styles.note}>
            {common === null
              ? "No match is shared by every model, so there is no common-comparison row."
              : `${formatCount(common.match_count)} matches are shared by every model, so the common rows compare them over the same fixtures.`}
          </p>
        </>
      )}

      {market === null ? (
        <p className={styles.note}>
          No shared match carried usable pre-closing odds, so no market
          reference is available.
        </p>
      ) : (
        <div className={styles.tableWrap}>
          <table className={styles.table} id="live-market">
            <caption className={styles.caption}>
              Market reference benchmark
            </caption>
            <thead>
              <tr>
                <th className={`${styles.cell} ${styles.headCell}`} scope="col">
                  Model
                </th>
                <th
                  className={`${styles.cell} ${styles.headCell} ${styles.numeric}`}
                  scope="col"
                >
                  Matches
                </th>
                <th
                  className={`${styles.cell} ${styles.headCell} ${styles.numeric}`}
                  scope="col"
                >
                  Log loss
                </th>
                <th
                  className={`${styles.cell} ${styles.headCell} ${styles.numeric}`}
                  scope="col"
                >
                  Brier
                </th>
                <th
                  className={`${styles.cell} ${styles.headCell} ${styles.numeric}`}
                  scope="col"
                >
                  RPS
                </th>
                <th
                  className={`${styles.cell} ${styles.headCell} ${styles.numeric}`}
                  scope="col"
                >
                  Accuracy
                </th>
                <th
                  className={`${styles.cell} ${styles.headCell} ${styles.numeric}`}
                  scope="col"
                >
                  Matches without odds
                </th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <th className={`${styles.cell} ${styles.rowHeader}`} scope="row">
                  {modelLabel(market.model)}
                </th>
                <td className={`${styles.cell} ${styles.numeric}`}>
                  {formatCount(market.match_count)}
                </td>
                {scoreCells(market.scores)}
                <td className={`${styles.cell} ${styles.numeric}`}>
                  {formatCount(market.matches_without_odds)}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      )}
      {/* The caveat holds in both cases: with no market reference there is no
          row to qualify, and the benchmark still was not a stored forecast. */}
      <p className={styles.note}>
        The market reference is a benchmark, not a stored forecast:{" "}
        {
          "market uses pre-closing odds published with the results; not stored before kickoff."
        }
      </p>
    </>
  );
}

type PerformancePageProps = {
  readonly searchParams: Promise<SearchParams>;
};

export default async function PerformancePage(
  props: PerformancePageProps,
): Promise<ReactElement> {
  const params = await props.searchParams;
  const activeSubset = parseSubset(params.subset);

  // One round trip each, both awaited together: a failure on one section must
  // not hide the other, and the page renders each result on its own.
  const [backtestResult, liveResult] = await Promise.all([
    get("/v1/model-metrics"),
    get("/v1/model-metrics/live"),
  ]);

  return (
    <div className={styles.main}>
      <div className={styles.header}>
        <h1 className={styles.title}>Performance</h1>
        <p className={styles.intro}>
          A stored walk-forward backtest and the forecasts the worker stored for
          the season, measured with the same error metrics so the two reports
          can be read side by side.
        </p>
      </div>

      <section className={styles.section} aria-labelledby="backtest-heading">
        <h2 className={styles.heading} id="backtest-heading">
          Backtest
        </h2>
        <SubsetControl active={activeSubset} />
        {backtestResult.ok ? (
          <BacktestReport data={backtestResult.data} subset={activeSubset} />
        ) : backtestResult.status === 404 ? (
          <EmptyState title="No backtest run yet" command="make backtest">
            <p>
              The API holds no succeeded walk-forward backtest, so there is no
              run to report and no metrics to plot.
            </p>
          </EmptyState>
        ) : (
          <ProblemState problem={backtestResult.problem} />
        )}
      </section>

      <section className={styles.section} aria-labelledby="live-heading">
        <h2 className={styles.heading} id="live-heading">
          Live predictions
        </h2>
        {liveResult.ok ? (
          <LiveReport data={liveResult.data} />
        ) : liveResult.status === 404 ? (
          <EmptyState title="No live metrics yet" command="make ingest">
            <p>
              The live report scores the predictions stored for the season the
              last ingestion loaded, and no season has a match yet.
            </p>
          </EmptyState>
        ) : (
          <ProblemState problem={liveResult.problem} />
        )}
      </section>
    </div>
  );
}
