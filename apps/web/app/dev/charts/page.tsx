import { notFound } from "next/navigation";
import type { ReactElement } from "react";

import {
  CalibrationChart,
  DotPlot,
  FormStrip,
  IntervalBars,
  LineChart,
  PositionHeatmap,
  ProbabilityBar,
  Sparkline,
  type CalibrationPoint,
} from "@/components/charts";

import styles from "./page.module.css";

export const dynamic = "force-dynamic";

const TEAMS: readonly string[] = [
  "Real Madrid",
  "Barcelona",
  "Atletico Madrid",
  "Athletic Club",
  "Villarreal",
  "Real Betis",
  "Real Sociedad",
  "Sevilla",
  "Valencia",
  "Girona",
  "Celta Vigo",
  "Osasuna",
  "Mallorca",
  "Rayo Vallecano",
  "Getafe",
  "Alaves",
  "Espanyol",
  "Las Palmas",
  "Leganes",
  "Valladolid",
];

// One expected finishing position per team, in the order above.
const EXPECTED_POSITIONS: readonly number[] = [
  1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20,
];

const CALIBRATION_POINTS: readonly CalibrationPoint[] = [
  { model: "home_win", outcome: "H", meanPredicted: 0.24, observedFrequency: 0.25, n: 342 },
  { model: "home_win", outcome: "H", meanPredicted: 0.48, observedFrequency: 0.46, n: 287 },
  { model: "home_win", outcome: "D", meanPredicted: 0.21, observedFrequency: 0.22, n: 311 },
  { model: "home_win", outcome: "D", meanPredicted: null, observedFrequency: null, n: 96 },
  { model: "home_win", outcome: "A", meanPredicted: 0.19, observedFrequency: 0.18, n: 254 },
  { model: "home_win", outcome: "A", meanPredicted: 0.37, observedFrequency: 0.4, n: 188 },
  { model: "market", outcome: "H", meanPredicted: 0.26, observedFrequency: 0.27, n: 401 },
  { model: "market", outcome: "H", meanPredicted: 0.51, observedFrequency: 0.5, n: 366 },
  { model: "market", outcome: "D", meanPredicted: 0.23, observedFrequency: 0.22, n: 344 },
  { model: "market", outcome: "D", meanPredicted: 0.29, observedFrequency: 0.29, n: 0 },
  { model: "market", outcome: "A", meanPredicted: 0.2, observedFrequency: 0.19, n: 298 },
  { model: "market", outcome: "A", meanPredicted: 0.39, observedFrequency: 0.41, n: 241 },
  { model: "dixon_coles", outcome: "H", meanPredicted: 0.25, observedFrequency: 0.24, n: 377 },
  { model: "dixon_coles", outcome: "H", meanPredicted: 0.49, observedFrequency: 0.49, n: 301 },
  { model: "dixon_coles", outcome: "D", meanPredicted: 0.22, observedFrequency: 0.23, n: 329 },
  { model: "dixon_coles", outcome: "D", meanPredicted: 0.31, observedFrequency: 0.3, n: 207 },
  { model: "dixon_coles", outcome: "A", meanPredicted: 0.18, observedFrequency: 0.17, n: 274 },
  { model: "dixon_coles", outcome: "A", meanPredicted: 0.36, observedFrequency: 0.38, n: 215 },
  { model: "elo", outcome: "H", meanPredicted: 0.27, observedFrequency: 0.28, n: 260 },
  { model: "elo", outcome: "D", meanPredicted: 0.24, observedFrequency: 0.23, n: 233 },
  { model: "elo", outcome: "A", meanPredicted: 0.21, observedFrequency: 0.2, n: 198 },
];

function buildPositionRow(expectedPosition: number): readonly number[] {
  const weights = Array.from({ length: TEAMS.length }, (_, columnIndex) => {
    const distance = columnIndex + 1 - expectedPosition;
    return Math.exp(-(distance * distance) / 4);
  });
  const total = weights.reduce((sum, weight) => sum + weight, 0);
  const row = weights.map((weight) => Math.round((weight / total) * 1000) / 1000);
  const peak = expectedPosition - 1;
  const drift = 1 - row.reduce((sum, value) => sum + value, 0);
  row[peak] = Number((row[peak] + drift).toFixed(3));
  return row;
}

const PROJECTED_TABLE: readonly (readonly number[])[] =
  EXPECTED_POSITIONS.map(buildPositionRow);

export default function ChartGalleryPage(): ReactElement {
  // Only the development stack sets this variable, so the gallery is absent
  // from every other deployment.
  if (process.env.LMI_DEV_PAGES !== "1") {
    notFound();
  }

  return (
    <main className={styles.main}>
      <header className={styles.header}>
        <h1 className={styles.title}>Chart gallery</h1>
        <p className={styles.intro}>
          Every dashboard chart with inline fixture data, so the rendering can be
          checked without a database or a live API. The data is illustrative and
          not a published forecast.
        </p>
      </header>

      <section className={styles.section} aria-labelledby="form-heading">
        <h2 id="form-heading" className={styles.heading}>
          Recent form
        </h2>
        <FormStrip
          id="dev-form-valencia"
          title="Valencia recent form: W, D, L, W, W"
          description="Valencia's five most recent results, newest first, lettered W for a win, D for a draw and L for a loss."
          note="The newest result is the leftmost square."
          results={["W", "D", "L", "W", "W"]}
        />
      </section>

      <section className={styles.section} aria-labelledby="probability-heading">
        <h2 id="probability-heading" className={styles.heading}>
          Home win probability
        </h2>
        <ProbabilityBar
          id="dev-probability-valencia-betis"
          title="Valencia vs Real Betis: match outcome probabilities"
          description="Stacked bar of the home win, draw and away win probabilities for Valencia at home against Real Betis."
          note="Valencia is the home team and Real Betis the away team."
          home={{ name: "Valencia", probability: 0.38 }}
          draw={{ probability: 0.31 }}
          away={{ name: "Real Betis", probability: 0.31 }}
        />
      </section>

      <section className={styles.section} aria-labelledby="positions-heading">
        <h2 id="positions-heading" className={styles.heading}>
          Projected table
        </h2>
        <PositionHeatmap
          id="dev-position-heatmap"
          title="Projected finishing positions, 20 teams"
          description="Heatmap of each team's probability of finishing in every league position. Rows follow the projected table; columns are positions 1 to 20."
          note="Each row sums to about 100 percent; the colour darkens with a higher probability."
          teams={TEAMS}
          positions={PROJECTED_TABLE}
          qualification={{ uclPositions: 4, europePositions: 6, relegationPositions: 3 }}
        />
      </section>

      <section className={styles.section} aria-labelledby="points-heading">
        <h2 id="points-heading" className={styles.heading}>
          Projected points
        </h2>
        <IntervalBars
          id="dev-projected-points"
          title="Projected final points with survival threshold"
          description="Projected final points per team: the thin bar spans the 5th to 95th percentile, the thick bar the interquartile range, the tick marks the median and the diamond marks current points."
          note="The dashed line is the 40-point survival threshold; diamonds mark points already won."
          threshold={40}
          thresholdLabel="Survival threshold"
          rows={[
            { team: "Real Madrid", p05: 78, p25: 84, p50: 88, p75: 92, p95: 97, current: 72 },
            { team: "Barcelona", p05: 74, p25: 80, p50: 84, p75: 89, p95: 94, current: 68 },
            { team: "Atletico Madrid", p05: 68, p25: 74, p50: 78, p75: 83, p95: 88, current: 63 },
            { team: "Villarreal", p05: 57, p25: 62, p50: 66, p75: 71, p95: 76, current: 51 },
            { team: "Real Betis", p05: 52, p25: 57, p50: 61, p75: 66, p95: 71, current: 47 },
            { team: "Valencia", p05: 34, p25: 39, p50: 44, p75: 49, p95: 55, current: 31 },
            { team: "Girona", p05: 31, p25: 36, p50: 41, p75: 46, p95: 52, current: 28 },
            { team: "Valladolid", p05: 22, p25: 27, p50: 32, p75: 37, p95: 43, current: 19 },
          ]}
        />
      </section>

      <section className={styles.section} aria-labelledby="progression-heading">
        <h2 id="progression-heading" className={styles.heading}>
          Points progression
        </h2>
        <LineChart
          id="dev-points-progression"
          title="Cumulative points against expected points"
          description="Valencia's cumulative points after each of the first eight matchdays, compared with the model's expected points before kickoff."
          note="The thick line is Valencia's actual cumulative points; the thin line is the pre-match expected total."
          xLabels={["MD1", "MD2", "MD3", "MD4", "MD5", "MD6", "MD7", "MD8"]}
          series={[
            { name: "Cumulative points", values: [3, 4, 7, 10, 11, 14, 17, 20], emphasis: true },
            { name: "Expected points", values: [1.4, 2.9, 4.3, 5.8, 7.1, 8.6, 10, 11.4] },
          ]}
          yLabel="Points"
        />
      </section>

      <section className={styles.section} aria-labelledby="relegation-heading">
        <h2 id="relegation-heading" className={styles.heading}>
          Relegation risk
        </h2>
        <LineChart
          id="dev-relegation-probability"
          title="Relegation probability by matchday"
          description="Model-estimated relegation probability for Valencia, Real Betis and Girona after each of the first eight matchdays."
          note="Valencia is the focus team; the probability rises for all three as the season narrows."
          xLabels={["MD1", "MD2", "MD3", "MD4", "MD5", "MD6", "MD7", "MD8"]}
          series={[
            { name: "Valencia", values: [12, 14, 18, 22, 27, 31, 36, 42], emphasis: true },
            { name: "Real Betis", values: [6, 7, 8, 9, 11, 13, 15, 18] },
            { name: "Girona", values: [9, 11, 13, 16, 19, 23, 27, 32] },
          ]}
          yLabel="Relegation probability"
          valueSuffix="%"
        />
      </section>

      <section className={styles.section} aria-labelledby="metrics-heading">
        <h2 id="metrics-heading" className={styles.heading}>
          Model metrics
        </h2>
        <DotPlot
          id="dev-model-metrics"
          title="Model metrics compared"
          description="One panel per metric, with one row per model. Filled dots show each model's value; dashed lines mark the home_win and market reference values."
          note="The dashed references mark the home_win and market values of each metric."
          metrics={["log_loss", "brier", "rps", "accuracy"]}
          rows={[
            { model: "home_win", values: [0.993, 0.605, 0.215, 0.505] },
            { model: "base_rate", values: [1.027, 0.631, 0.232, 0.455] },
            { model: "market", values: [0.971, 0.589, 0.201, 0.521] },
            { model: "elo", values: [0.989, 0.601, 0.209, 0.512] },
            { model: "dixon_coles", values: [0.985, 0.598, 0.206, 0.516] },
          ]}
          references={[
            { metric: "log_loss", label: "home_win", value: 0.993 },
            { metric: "log_loss", label: "market", value: 0.971 },
            { metric: "brier", label: "home_win", value: 0.605 },
            { metric: "brier", label: "market", value: 0.589 },
            { metric: "rps", label: "home_win", value: 0.215 },
            { metric: "rps", label: "market", value: 0.201 },
            { metric: "accuracy", label: "home_win", value: 0.505 },
            { metric: "accuracy", label: "market", value: 0.521 },
          ]}
        />
      </section>

      <section className={styles.section} aria-labelledby="calibration-heading">
        <h2 id="calibration-heading" className={styles.heading}>
          Calibration
        </h2>
        <CalibrationChart
          id="dev-calibration"
          title="Calibration by outcome, three models"
          description="Calibration by outcome: mean predicted probability against observed frequency for home wins, draws and away wins. The diagonal marks perfect calibration; dot area grows with the number of matches."
          note="The models filter keeps home_win, market and dixon_coles and drops the elo points. Bins with a null value or no matches are skipped."
          models={["home_win", "market", "dixon_coles"]}
          points={CALIBRATION_POINTS}
        />
      </section>

      <section className={styles.section} aria-labelledby="goals-heading">
        <h2 id="goals-heading" className={styles.heading}>
          Goals per match
        </h2>
        <Sparkline
          id="dev-goals-per-match"
          title="Goals per match since 2005-06"
          description="Average goals per La Liga match in each season from 2005-06 to 2018-19, with the largest season-to-season drop highlighted."
          note="The trend declines from 2.62 goals per match in 2005-06 to 2.35 in 2018-19."
          metric="Goals per match"
          seasons={[
            "2005-06",
            "2006-07",
            "2007-08",
            "2008-09",
            "2009-10",
            "2010-11",
            "2011-12",
            "2012-13",
            "2013-14",
            "2014-15",
            "2015-16",
            "2016-17",
            "2017-18",
            "2018-19",
          ]}
          values={[2.62, 2.58, 2.55, 2.61, 2.49, 2.53, 2.48, 2.56, 2.51, 2.58, 2.44, 2.39, 2.47, 2.35]}
        />
      </section>
    </main>
  );
}
