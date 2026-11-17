/**
 * The performance page: the stored walk-forward backtest and the live forecast
 * report, side by side.
 *
 * Both reports come from the API and share one metric vocabulary, so the page
 * fetches them in parallel and renders each section in its own state. A section
 * whose report is missing says so and names the command that produces it,
 * instead of drawing an empty table or a zero nobody measured.
 *
 * Every choice a reader makes -- the match subset and the drawn calibration
 * model -- lives in the URL, so the page stays a Server Component: no control
 * here holds client state, and a shared link reproduces the whole report.
 */
import Link from "next/link";
import { Fragment, type ReactElement } from "react";

import { LineChart, type LineSeries } from "@/components/charts";
import { EmptyState, ProblemState } from "@/components/states";
import {
  MetricHelp,
  SectionNav,
  StatTile,
  type MetricHelpProps,
  type SectionNavItem,
} from "@/components/ui";
import { get, type GetData } from "@/lib/api";
import { EM_DASH, formatCount, formatPercent, formatValue } from "@/lib/format";
import { modelLabel } from "@/lib/labels";
import {
  parseSubset,
  type MetricsSubset,
  type SearchParams,
} from "@/lib/params";

import {
  CalibrationPanels,
  type CalibrationModel,
  type CalibrationPoint,
} from "./CalibrationPanels";
import { RankingBars, type RankingBarRow } from "./RankingBars";
import {
  best,
  inScaleDomain,
  isBestValue,
  kindLabel,
  kindLine,
  offScaleRows,
  overallRows,
  parseCalibration,
  seasonRps,
  seasonsOf,
  subsetHref,
  subsetLabel,
  tightDomain,
  verdictText,
  type BacktestData,
  type OverallRow,
  type ScoreKey,
} from "./metrics";
import styles from "./page.module.css";

export const dynamic = "force-dynamic";

type LiveData = GetData<"/v1/model-metrics/live">;
type LiveScores = LiveData["models"][number]["scores"];

/** The two directions a metric help states. */
type HelpDirection = NonNullable<MetricHelpProps["direction"]>;

/** The one direction a score column is best in. */
type ScoreDirection = "min" | "max";

// The definitions behind the help controls, written once so the tiles, the
// table headers and the ranking heading cannot drift apart.
const RPS_DEFINITION =
  "Ranked probability score: how far the forecast H/D/A probabilities were from what happened, counting a draw as between a home and an away win. 0 is perfect.";

const LOG_LOSS_DEFINITION =
  "Punishes confident mistakes hard: a forecast that says 100 % and is wrong is scored as infinitely bad.";

const BRIER_DEFINITION =
  "Average squared gap between each probability and what happened (1 or 0). Easy to read, gentler than log loss.";

const ACCURACY_DEFINITION =
  "How often the most likely outcome happened. Intuitive but blunt: it ignores how confident the forecast was.";

const COVERAGE_DEFINITION =
  "Share of the fixtures in the window this model forecast (n_matches / n_fixtures).";

/** The muted line under the backtest metrics table. */
const TABLE_NOTE =
  "Log loss, Brier and RPS are errors, so lower is better; accuracy is a hit rate, so higher is better.";

/** What the phone layout leaves out of the backtest metrics table. */
const PHONE_NOTE =
  "Kind, Brier, Matches and Coverage appear on a wider screen; Brier's best value is in the tiles above.";

/** One of the four scores, as a tile and as a table column both read it. */
type ScoreColumn = {
  readonly key: ScoreKey;
  readonly label: string;
  /** Whether the best value is the smallest or the largest one. */
  readonly direction: ScoreDirection;
  readonly definition: string;
  readonly better: HelpDirection;
  /** The scale sentence under the headline value of the tile. */
  readonly scale: string;
  /** Whether the column is dropped on a phone. */
  readonly lowPriority: boolean;
};

// The four scores in the order the report explains them, which is also the
// order of the tiles and of the score columns of the table.
const SCORE_COLUMNS: readonly ScoreColumn[] = [
  {
    key: "rps",
    label: "RPS",
    direction: "min",
    definition: RPS_DEFINITION,
    better: "lower-is-better",
    scale: "Lower is better, 0 to 1.",
    lowPriority: false,
  },
  {
    key: "log_loss",
    label: "Log loss",
    direction: "min",
    definition: LOG_LOSS_DEFINITION,
    better: "lower-is-better",
    scale: "Lower is better, 0 to ∞.",
    lowPriority: false,
  },
  {
    key: "brier",
    label: "Brier",
    direction: "min",
    definition: BRIER_DEFINITION,
    better: "lower-is-better",
    scale: "Lower is better, 0 to 2.",
    lowPriority: true,
  },
  {
    key: "accuracy",
    label: "Accuracy",
    direction: "max",
    definition: ACCURACY_DEFINITION,
    better: "higher-is-better",
    scale: "Higher is better.",
    lowPriority: false,
  },
];

/** The four scores of the live tables, in the order those tables print them. */
const LIVE_SCORE_KEYS: readonly ScoreKey[] = [
  "log_loss",
  "brier",
  "rps",
  "accuracy",
];

/** Join the class names of one element, skipping the ones that do not apply. */
function classes(...names: readonly (string | false)[]): string {
  return names.filter((name): name is string => name !== false).join(" ");
}

/** Return a stored string, or an em dash when the API stored none. */
function textValue(value: string | null | undefined): string {
  return value === null || value === undefined || value.trim() === ""
    ? EM_DASH
    : value;
}

/** Print one score the way the report prints it. */
function formatScore(key: ScoreKey, value: number | null): string {
  return key === "accuracy" ? formatPercent(value) : formatValue(value, 3);
}

/** The first row that holds the best value of one score, within 1e-9. */
function bestRow(
  rows: readonly OverallRow[],
  key: ScoreKey,
  direction: ScoreDirection,
): OverallRow | undefined {
  const extreme = best(rows, key, direction);
  if (extreme === null) {
    return undefined;
  }
  return rows.find((row) => Math.abs(row.metric[key] - extreme) <= 1e-9);
}

/**
 * The payload's calibration bins grouped by model, in the run's model order.
 *
 * A model the run lists without a single bin is left out, so the picker never
 * offers a panel it could not draw.
 */
function calibrationModelsOf(data: BacktestData): readonly CalibrationModel[] {
  const models: CalibrationModel[] = [];
  for (const model of data.models) {
    const points: CalibrationPoint[] = [];
    for (const bin of data.calibration) {
      if (bin.model === model.name) {
        points.push({
          outcome: bin.outcome,
          binIndex: bin.bin_index,
          meanPredicted: bin.mean_predicted,
          observedFrequency: bin.observed_frequency,
          n: bin.n,
        });
      }
    }
    if (points.length > 0) {
      models.push({
        name: model.name,
        label: modelLabel(model.name),
        points,
      });
    }
  }
  return models;
}

/**
 * The calibration model the page draws.
 *
 * The URL wins when it names a model that holds a bin, because that is the
 * panel the reader asked for. Otherwise the page falls back to the first fitted
 * model and then to the first model of the run, which keeps the section filled
 * without a second choice the reader has to make. No model means no section.
 */
function selectedCalibrationOf(
  data: BacktestData | null,
  models: readonly CalibrationModel[],
  requested: string | null,
): string | null {
  if (models.length === 0) {
    return null;
  }
  const chosen = models.find((model) => model.name === requested);
  if (chosen !== undefined) {
    return chosen.name;
  }
  const firstFitted = models.find(
    (model) =>
      data?.models.some(
        (entry) => entry.name === model.name && entry.kind === "model",
      ) === true,
  );
  return (firstFitted ?? models[0]).name;
}

/**
 * One header cell that prints a metric name and its definition.
 *
 * The help control sits beside the name instead of inside it, so the column
 * header reads as "RPS" and the control carries its own accessible name.
 */
function HelpHeadCell(props: {
  readonly label: string;
  readonly definition: string;
  readonly direction: HelpDirection;
  readonly className: string;
}): ReactElement {
  const { label, definition, direction, className } = props;

  return (
    <th className={className} scope="col">
      <span className={styles.headLabel}>
        {label}
        <MetricHelp
          label={label}
          definition={definition}
          direction={direction}
        />
      </span>
    </th>
  );
}

/**
 * The two links that choose the backtest subset.
 *
 * It is a list of links rather than a second navigation landmark: the shell
 * owns the one navigation region, and a page-level filter is not site
 * navigation. The current choice carries `aria-current`, so its state reaches
 * assistive technology as well as the eye. Every link keeps the drawn
 * calibration model, so switching the subset never loses the panel above.
 */
function SubsetControl(props: {
  readonly active: MetricsSubset;
  readonly calibration: string | null;
}): ReactElement {
  const { active, calibration } = props;
  const subsets: readonly MetricsSubset[] = ["all", "common"];

  return (
    <div className={styles.subsets}>
      <ul className={styles.subsetList}>
        {subsets.map((subset) => (
          <li key={subset}>
            <Link
              className={styles.subsetLink}
              href={subsetHref(subset, calibration)}
              aria-current={subset === active ? "page" : undefined}
            >
              {subsetLabel(subset)}
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
 * The backtest report: the ranking, the four scores, the metrics table, the
 * seasonal lines, the calibration panels and the run's provenance.
 *
 * The overall rows are filtered here rather than ordered by the API: the
 * payload holds them season by season, and the page needs the overall rows of
 * one subset in the model order the run states, so a table row and a model
 * entry always name the same model. Every block below is computed from that one
 * list, which is why a subset with no row shows one honest empty state instead
 * of five empty drawings.
 */
function BacktestReport(props: {
  readonly data: BacktestData;
  readonly subset: MetricsSubset;
  readonly calibrationModels: readonly CalibrationModel[];
  readonly selectedCalibration: string | null;
}): ReactElement {
  const { data, subset, calibrationModels, selectedCalibration } = props;

  const rows = overallRows(data, subset);
  const seasons = seasonsOf(data, subset);
  const offScale = new Set(offScaleRows(rows));
  const rankingDomain = inScaleDomain(rows);
  const otherSubset: MetricsSubset = subset === "all" ? "common" : "all";

  // The ranking order the bars draw: lowest RPS first, and the payload order
  // breaks a tie, which is why the mapped list is sorted instead of the rows.
  const rankingRows: readonly RankingBarRow[] = rows
    .map((row) => ({
      name: modelLabel(row.model.name),
      kind: kindLine(row.model.kind),
      rps: row.metric.rps,
      offScale: offScale.has(row),
    }))
    .sort((left, right) => left.rps - right.rps);

  // One line per model for the seasonal chart, in the run's own order, so the
  // series colours stay put whichever subset is chosen.
  const series: readonly LineSeries[] = data.models.slice(0, 5).map((model) => ({
    name: modelLabel(model.name),
    values: seasons.map((season) =>
      seasonRps(data, subset, model.name, season),
    ),
    offScale: rows.some(
      (row) => row.model.name === model.name && offScale.has(row),
    ),
  }));
  const seasonDomain = tightDomain(
    series
      .filter((line) => line.offScale !== true)
      .flatMap((line) => line.values)
      .filter(
        (value): value is number =>
          typeof value === "number" && Number.isFinite(value),
      ),
  );
  const offScaleNames = series
    .filter((line) => line.offScale === true)
    .map((line) => line.name);

  const matchCounts = rows.map((row) => row.metric.n_matches);
  const everyMatchCount = matchCounts.every((count) => count === matchCounts[0]);
  const caption =
    `Overall backtest metrics, ${subsetLabel(subset).toLowerCase()}. ` +
    "Best value per column in bold." +
    (everyMatchCount
      ? ` Every model scored all ${formatCount(matchCounts[0])} matches.`
      : ` Models scored ${formatCount(Math.min(...matchCounts))}–${formatCount(
          Math.max(...matchCounts),
        )} matches; Common matches compares identical fixtures.`);

  const emptyProse =
    subset === "all"
      ? "The latest succeeded run stores no overall row for the forecasts of every model. "
      : "The latest succeeded run stores no overall row for the fixtures every model scored. ";

  return (
    <>
      {rows.length === 0 ? (
        <EmptyState title="No metrics for this subset">
          <p>
            {emptyProse}
            <Link href={subsetHref(otherSubset, selectedCalibration)}>
              {subsetLabel(otherSubset)}
            </Link>{" "}
            reads the other match set.
          </p>
        </EmptyState>
      ) : (
        <>
          <div className={styles.subsection} id="ranking">
            <div className={styles.subheadingRow}>
              <h3 className={styles.subheading} id="ranking-heading">
                Ranking by RPS
              </h3>
              <MetricHelp
                label="RPS"
                definition={RPS_DEFINITION}
                direction="lower-is-better"
              />
            </div>
            <RankingBars
              title={`RPS by model, ${subsetLabel(subset).toLowerCase()}`}
              rows={rankingRows}
              domain={rankingDomain}
            />
          </div>

          <section className={styles.subsection} id="scores">
            <h3 className={styles.subheading}>The four scores, explained</h3>
            <div className={styles.scoreGrid}>
              {SCORE_COLUMNS.map((column) => {
                const row = bestRow(rows, column.key, column.direction);
                const value = best(rows, column.key, column.direction);
                return (
                  <StatTile
                    key={column.key}
                    label={column.label}
                    value={formatScore(column.key, value)}
                    subline={`Best: ${
                      row === undefined
                        ? EM_DASH
                        : modelLabel(row.metric.model)
                    }. ${column.scale}`}
                    help={{
                      definition: column.definition,
                      direction: column.better,
                    }}
                  />
                );
              })}
            </div>
          </section>

          <div className={styles.subsection} id="all-scores">
            <div className={styles.tableWrap}>
              <table className={styles.table} id="backtest-metrics">
                <caption className={styles.caption}>{caption}</caption>
                <thead>
                  <tr>
                    <th className={classes(styles.cell, styles.headCell)} scope="col">
                      Model
                    </th>
                    <th
                      className={classes(
                        styles.cell,
                        styles.headCell,
                        styles.lowPriority,
                      )}
                      scope="col"
                    >
                      Kind
                    </th>
                    {SCORE_COLUMNS.map((column) => (
                      <HelpHeadCell
                        key={column.key}
                        label={column.label}
                        definition={column.definition}
                        direction={column.better}
                        className={classes(
                          styles.cell,
                          styles.headCell,
                          styles.numeric,
                          column.lowPriority && styles.lowPriority,
                        )}
                      />
                    ))}
                    <th
                      className={classes(
                        styles.cell,
                        styles.headCell,
                        styles.numeric,
                        styles.lowPriority,
                      )}
                      scope="col"
                    >
                      Matches
                    </th>
                    <HelpHeadCell
                      label="Coverage"
                      definition={COVERAGE_DEFINITION}
                      direction="higher-is-better"
                      className={classes(
                        styles.cell,
                        styles.headCell,
                        styles.numeric,
                        styles.lowPriority,
                      )}
                    />
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.model.name}>
                      <th
                        className={classes(styles.cell, styles.rowHeader)}
                        scope="row"
                      >
                        {modelLabel(row.model.name)}
                      </th>
                      <td
                        className={classes(
                          styles.cell,
                          styles.lowPriority,
                        )}
                      >
                        {kindLabel(row.model.kind)}
                      </td>
                      {SCORE_COLUMNS.map((column) => {
                        const value = row.metric[column.key];
                        const text = formatScore(column.key, value);
                        return (
                          <td
                            key={column.key}
                            className={classes(
                              styles.cell,
                              styles.numeric,
                              column.lowPriority && styles.lowPriority,
                            )}
                          >
                            {isBestValue(
                              rows,
                              column.key,
                              column.direction,
                              value,
                            ) ? (
                              <>
                                <strong>{text}</strong>
                                <span className={styles.srOnly}> (best)</span>
                              </>
                            ) : (
                              text
                            )}
                          </td>
                        );
                      })}
                      <td
                        className={classes(
                          styles.cell,
                          styles.numeric,
                          styles.lowPriority,
                        )}
                      >
                        {formatCount(row.metric.n_matches)}
                      </td>
                      <td
                        className={classes(
                          styles.cell,
                          styles.numeric,
                          styles.lowPriority,
                        )}
                      >
                        {formatPercent(row.metric.coverage)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className={styles.note}>{TABLE_NOTE}</p>
            <p className={styles.phoneNote}>{PHONE_NOTE}</p>
          </div>

          {seasons.length > 0 ? (
            <div className={styles.subsection} id="by-season">
              <h3 className={styles.subheading}>Season by season</h3>
              <LineChart
                id="backtest-rps-by-season"
                title="RPS by season"
                description="Ranked probability score of each model in every scored season of the backtest. A lower line is better, and a gap means the model scored no match in that season."
                yLabel="RPS"
                xLabels={seasons}
                series={series}
                domain={seasonDomain}
                note={`Axis ${formatValue(seasonDomain[0], 3)}–${formatValue(
                  seasonDomain[1],
                  3,
                )}${
                  offScaleNames.length > 0
                    ? `; ${offScaleNames.join(", ")} off the scale`
                    : ""
                }.`}
              />
            </div>
          ) : null}

          {calibrationModels.length > 0 && selectedCalibration !== null ? (
            <section
              className={classes(styles.subsection, styles.calibrationCard)}
              id="calibration"
            >
              <h3 className={styles.subheading}>
                Are the probabilities honest?
              </h3>
              <CalibrationPanels
                models={calibrationModels}
                selected={selectedCalibration}
                subset={subset}
              />
            </section>
          ) : null}
        </>
      )}

      {/* The provenance is not a metric: it answers how the run was made, so it
          stays closed until a reader asks, and it stays available even when the
          subset above has no row to show. */}
      <details className={styles.repro} id="reproducibility">
        <summary className={styles.reproSummary}>
          Reproducibility: how this backtest was run
        </summary>
        <dl className={styles.pairs}>
          <dt className={styles.term}>Matches scored</dt>
          <dd className={styles.value}>{formatCount(data.run.n_matches)}</dd>
          <dt className={styles.term}>From year</dt>
          <dd className={styles.value}>{formatCount(data.run.from_year)}</dd>
          <dt className={styles.term}>Step days</dt>
          <dd className={styles.value}>{formatCount(data.run.step_days)}</dd>
          <dt className={styles.term}>Seed</dt>
          <dd className={styles.value}>{formatCount(data.run.seed)}</dd>
          <dt className={styles.term}>Code version</dt>
          <dd className={styles.value}>
            <code className={styles.code}>
              {textValue(data.run.code_version)}
            </code>
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
      </details>
    </>
  );
}

/**
 * The four scores of one live row.
 *
 * Log loss and Brier are the two the phone layout drops, so the caller states
 * which of the four a narrow screen may not show.
 */
function scoreCells(props: {
  readonly scores: LiveScores;
  readonly lowPriority: readonly ScoreKey[];
}): ReactElement {
  const { scores, lowPriority } = props;

  return (
    <>
      {LIVE_SCORE_KEYS.map((key) => (
        <td
          key={key}
          className={classes(
            styles.cell,
            styles.numeric,
            lowPriority.includes(key) && styles.lowPriority,
          )}
        >
          {formatScore(key, scores[key])}
        </td>
      ))}
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
        <dd className={styles.value}>
          {formatCount(data.counts.n_superseded)}
        </dd>
        <dt className={styles.term}>Withdrawn</dt>
        <dd className={styles.value}>
          {formatCount(data.counts.n_withdrawn)}
        </dd>
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
                  <th className={classes(styles.cell, styles.headCell)} scope="col">
                    Model
                  </th>
                  <th className={classes(styles.cell, styles.headCell)} scope="col">
                    Match set
                  </th>
                  <th
                    className={classes(
                      styles.cell,
                      styles.headCell,
                      styles.numeric,
                      styles.lowPriority,
                    )}
                    scope="col"
                  >
                    Matches
                  </th>
                  <th
                    className={classes(
                      styles.cell,
                      styles.headCell,
                      styles.numeric,
                      styles.lowPriority,
                    )}
                    scope="col"
                  >
                    Log loss
                  </th>
                  <th
                    className={classes(
                      styles.cell,
                      styles.headCell,
                      styles.numeric,
                      styles.lowPriority,
                    )}
                    scope="col"
                  >
                    Brier
                  </th>
                  <th
                    className={classes(styles.cell, styles.headCell, styles.numeric)}
                    scope="col"
                  >
                    RPS
                  </th>
                  <th
                    className={classes(styles.cell, styles.headCell, styles.numeric)}
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
                      className={classes(styles.cell, styles.rowHeader)}
                      scope="row"
                    >
                      {modelLabel(entry.model)}
                    </th>
                    <td className={styles.cell}>Own rows</td>
                    <td
                      className={classes(
                        styles.cell,
                        styles.numeric,
                        styles.lowPriority,
                      )}
                    >
                      {formatCount(entry.scores.n_matches)}
                    </td>
                    {scoreCells({
                      scores: entry.scores,
                      lowPriority: ["log_loss", "brier"],
                    })}
                  </tr>
                ))}
                {(common?.models ?? []).map((entry) => (
                  <tr key={`common-${entry.model}`}>
                    <th
                      className={classes(styles.cell, styles.rowHeader)}
                      scope="row"
                    >
                      {modelLabel(entry.model)}
                    </th>
                    <td className={styles.cell}>Common matches</td>
                    <td
                      className={classes(
                        styles.cell,
                        styles.numeric,
                        styles.lowPriority,
                      )}
                    >
                      {formatCount(entry.scores.n_matches)}
                    </td>
                    {scoreCells({
                      scores: entry.scores,
                      lowPriority: ["log_loss", "brier"],
                    })}
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
        <>
          <div className={styles.tableWrap}>
            <table className={styles.table} id="live-market">
              <caption className={styles.caption}>
                Market reference benchmark
              </caption>
              <thead>
                <tr>
                  <th className={classes(styles.cell, styles.headCell)} scope="col">
                    Model
                  </th>
                  <th
                    className={classes(styles.cell, styles.headCell, styles.numeric)}
                    scope="col"
                  >
                    Matches
                  </th>
                  <th
                    className={classes(
                      styles.cell,
                      styles.headCell,
                      styles.numeric,
                      styles.lowPriority,
                    )}
                    scope="col"
                  >
                    Log loss
                  </th>
                  <th
                    className={classes(
                      styles.cell,
                      styles.headCell,
                      styles.numeric,
                      styles.lowPriority,
                    )}
                    scope="col"
                  >
                    Brier
                  </th>
                  <th
                    className={classes(styles.cell, styles.headCell, styles.numeric)}
                    scope="col"
                  >
                    RPS
                  </th>
                  <th
                    className={classes(styles.cell, styles.headCell, styles.numeric)}
                    scope="col"
                  >
                    Accuracy
                  </th>
                  <th
                    className={classes(
                      styles.cell,
                      styles.headCell,
                      styles.numeric,
                      styles.lowPriority,
                    )}
                    scope="col"
                  >
                    Matches without odds
                  </th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <th
                    className={classes(styles.cell, styles.rowHeader)}
                    scope="row"
                  >
                    {modelLabel(market.model)}
                  </th>
                  <td className={classes(styles.cell, styles.numeric)}>
                    {formatCount(market.match_count)}
                  </td>
                  {scoreCells({
                    scores: market.scores,
                    lowPriority: ["log_loss", "brier"],
                  })}
                  <td
                    className={classes(
                      styles.cell,
                      styles.numeric,
                      styles.lowPriority,
                    )}
                  >
                    {formatCount(market.matches_without_odds)}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          {/* The sentence names the matches the benchmark could not price, so
              a reader sees why its row covers fewer fixtures than the models'.
              The space is explicit: a text node that starts on the next line
              would lose it. */}
          <p className={styles.note}>
            {formatCount(market.matches_without_odds)}{" "}
            shared matches had no usable odds.
          </p>
        </>
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

  // The calibration choice is resolved once, here, so the header, the subset
  // links and the panels all read the same model name.
  const calibrationModels: readonly CalibrationModel[] = backtestResult.ok
    ? calibrationModelsOf(backtestResult.data)
    : [];
  const selectedCalibration = selectedCalibrationOf(
    backtestResult.ok ? backtestResult.data : null,
    calibrationModels,
    parseCalibration(params.calibration),
  );

  // The in-page navigation lists what this render actually drew, so no link
  // points at a block a missing report hid.
  const sections: SectionNavItem[] = [];
  if (!backtestResult.ok) {
    sections.push({ id: "backtest", label: "Backtest" });
  } else if (overallRows(backtestResult.data, activeSubset).length === 0) {
    sections.push(
      { id: "backtest", label: "Backtest" },
      { id: "reproducibility", label: "Reproducibility" },
    );
  } else {
    sections.push(
      { id: "ranking", label: "Ranking" },
      { id: "scores", label: "Scores" },
      { id: "all-scores", label: "All scores" },
    );
    if (seasonsOf(backtestResult.data, activeSubset).length > 0) {
      sections.push({ id: "by-season", label: "Season by season" });
    }
    if (calibrationModels.length > 0) {
      sections.push({ id: "calibration", label: "Calibration" });
    }
    sections.push({ id: "reproducibility", label: "Reproducibility" });
  }
  sections.push({ id: "live", label: "Live predictions" });

  // The sentence under the title names the scope of the run: how many matches
  // it covered, from which season, and that no forecast could see its own match.
  const allSeasons = backtestResult.ok ? seasonsOf(backtestResult.data) : [];
  const lede = backtestResult.ok
    ? `Walk-forward backtest over ${formatCount(
        backtestResult.data.run.n_matches,
      )} matches since ${
        allSeasons[0] ?? backtestResult.data.run.from_year
      }, refit every ${formatCount(
        backtestResult.data.run.step_days,
      )} days; each forecast used only matches played before it.`
    : null;

  return (
    <div className={styles.main}>
      <header className={styles.header}>
        <h1 className={styles.title}>Performance</h1>
        {backtestResult.ok && lede !== null ? (
          <>
            <p className={styles.lede}>{lede}</p>
            <p className={styles.verdict} id="performance-verdict">
              {verdictText(backtestResult.data)}
            </p>
          </>
        ) : (
          <p className={styles.intro}>
            A stored walk-forward backtest and the forecasts the worker stored
            for the season, measured with the same error metrics so the two
            reports can be read side by side.
          </p>
        )}
      </header>

      <SectionNav sections={sections} />

      <section
        id="backtest"
        className={styles.section}
        aria-labelledby="backtest-heading"
      >
        <h2 className={styles.heading} id="backtest-heading">
          Backtest
        </h2>
        <SubsetControl active={activeSubset} calibration={selectedCalibration} />
        {backtestResult.ok ? (
          <BacktestReport
            data={backtestResult.data}
            subset={activeSubset}
            calibrationModels={calibrationModels}
            selectedCalibration={selectedCalibration}
          />
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

      <section id="live" className={styles.section} aria-labelledby="live-heading">
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
