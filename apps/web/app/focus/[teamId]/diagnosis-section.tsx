/**
 * "Season diagnosis": where the club's season came from, group by group.
 *
 * The section reads one endpoint and renders it in its own state, so a rejected
 * or empty answer stays inside this section instead of taking the rest of the
 * page down. Every average is the API's own per-match average: the page prints
 * the number it was given and never divides anything itself, so a card cannot
 * disagree with the payload it came from. A group that played nothing has no
 * average and shows an em dash, never a zero.
 *
 * Two cards draw bars on a shared points-per-match scale instead of a table
 * grid: an average of 0 to 3 points is a ratio a reader knows, so the bars are
 * labelled with their value and the whole record stays in the figure's data
 * table behind the disclosure.
 */
import type { ReactElement } from "react";

import { ChartFigure, LineChart, chartIds, type LineSeries } from "@/components/charts";
import { SeasonSelect, type SeasonOption } from "@/components/shell";
import { EmptyState, ProblemState } from "@/components/states";
import { MetricHelp } from "@/components/ui";
import { formatCount, formatSigned, formatValue } from "@/lib/format";
import { formatMadridDate } from "@/lib/madrid";

import {
  loadSeasonStats,
  type SeasonRecord,
  type TeamSeasonStats,
} from "./data";
import styles from "./page.module.css";

/** The label the `<Suspense>` boundary of this section shows while it reads. */
export const SECTION_LABEL = "Season diagnosis";

export type DiagnosisSectionProps = {
  readonly teamId: number;
  /** The season the page resolved, never the raw query value. */
  readonly season: string;
  /** The seasons the selector offers when this one has no data. */
  readonly seasonOptions: readonly SeasonOption[];
};

/** The highest points-per-match average the shared bar scale can show. */
const MAX_POINTS_PER_GAME = 3;

/** The fewest matches a bar's average is worth drawing from. */
const THIN_SAMPLE = 3;

/** One bar of a points-per-match figure. */
type BarRow = {
  readonly label: string;
  readonly record: SeasonRecord;
};

/** The nine counter columns of a record, in the order the tables print them. */
const RECORD_COLUMNS = ["P", "W", "D", "L", "GF", "GA", "GD", "Pts", "PPG"];

/** One record as the nine columns of a data table. */
function recordCells(record: SeasonRecord): readonly string[] {
  return [
    formatCount(record.played),
    formatCount(record.won),
    formatCount(record.drawn),
    formatCount(record.lost),
    formatCount(record.goals_for),
    formatCount(record.goals_against),
    formatSigned(record.goals_for - record.goals_against),
    formatCount(record.points),
    formatValue(record.points_per_game, 2),
  ];
}

/**
 * The bars of one points-per-match figure.
 *
 * The scale is fixed at 0 to 3 for every row, because a point per match is the
 * one unit the two cards share: a scale that fitted each card to its own widest
 * row would make 1.40 look like a maximum. A group without an average keeps its
 * row, prints "no match" and draws no bar, so the row is visible as a gap.
 */
function PointsPerMatchBars(props: {
  readonly rows: readonly BarRow[];
  readonly titleId: string;
  readonly descriptionId: string;
}): ReactElement {
  const { rows, titleId, descriptionId } = props;

  return (
    <div
      className={styles.splitBars}
      role="img"
      aria-labelledby={titleId}
      aria-describedby={descriptionId}
    >
      {rows.map((row) => {
        const average = row.record.points_per_game;
        const share =
          average === null
            ? 0
            : Math.min(100, Math.max(0, (average / MAX_POINTS_PER_GAME) * 100));
        return (
          <div className={styles.splitRow} key={row.label}>
            <span className={styles.splitLabel}>{row.label}</span>
            <span className={styles.splitTrack}>
              {average === null ? null : (
                <span
                  className={styles.splitBar}
                  style={{ width: `${share}%` }}
                />
              )}
            </span>
            <span className={styles.splitValue}>
              {average === null ? "no match" : formatValue(average, 2)}
            </span>
          </div>
        );
      })}
      <p className={styles.splitScale}>
        Points per match, scale 0–{formatCount(MAX_POINTS_PER_GAME)}
      </p>
    </div>
  );
}

/** The card heading and its definition, side by side and never nested. */
function CardHead(props: {
  readonly label: string;
  readonly definition?: string;
}): ReactElement {
  const { label, definition } = props;
  return (
    <div className={styles.cardHead}>
      <h3 className={styles.cardTitle}>{label}</h3>
      {definition === undefined ? null : (
        <MetricHelp definition={definition} label={label} />
      )}
    </div>
  );
}

/** The venue split: one sentence, one figure and the full record behind it. */
function HomeAndAwayCard(props: {
  readonly data: TeamSeasonStats;
}): ReactElement {
  const { data } = props;
  const rows: readonly BarRow[] = [
    { label: "Home", record: data.splits.home },
    { label: "Away", record: data.splits.away },
  ];
  const caption = `Home and away record, ${data.season} season`;
  const ids = chartIds("focus-home-away");

  return (
    <div className={styles.card}>
      <CardHead label="Home and away" />
      <p className={styles.cardNote}>
        {`${formatCount(data.splits.home.points)} points from ${formatCount(
          data.splits.home.played,
        )} home games, ${formatCount(data.splits.away.points)} from ${formatCount(
          data.splits.away.played,
        )} away.`}
      </p>
      <ChartFigure
        description={`Points per match of the ${data.season} season at home and away, on a scale from 0 to ${formatCount(MAX_POINTS_PER_GAME)} points.`}
        ids={ids}
        table={{
          caption,
          columns: ["Venue", ...RECORD_COLUMNS],
          rows: rows.map((row) => [row.label, ...recordCells(row.record)]),
        }}
        title={caption}
      >
        <PointsPerMatchBars
          descriptionId={ids.descId}
          rows={rows}
          titleId={ids.titleId}
        />
      </ChartFigure>
    </div>
  );
}

/** The tier split: the same figure over the opponent's table position. */
function TiersCard(props: { readonly data: TeamSeasonStats }): ReactElement {
  const { data } = props;
  const rows: readonly BarRow[] = [
    { label: "Top", record: data.tiers.top },
    { label: "Middle", record: data.tiers.middle },
    { label: "Bottom", record: data.tiers.bottom },
  ];
  const caption = `Record against top, middle and bottom opponents, ${data.season} season`;
  const ids = chartIds("focus-tiers");
  const thin = rows
    .filter((row) => row.record.played < THIN_SAMPLE)
    .map((row) => `${row.label.toLowerCase()}: only ${formatCount(row.record.played)} game(s) so far`);

  return (
    <div className={styles.card}>
      <CardHead
        definition="Opponents split by their current table position: the top third of the table, the middle third and the bottom third. Each bar is the club's points per match against that tier, whatever the venue."
        label="Against whom"
      />
      <ChartFigure
        description={`Points per match of the ${data.season} season against the top, middle and bottom third of the table, on a scale from 0 to ${formatCount(MAX_POINTS_PER_GAME)} points.`}
        ids={ids}
        note={
          thin.length === 0
            ? undefined
            : `Thin samples: ${thin.join("; ")}.`
        }
        table={{
          caption,
          columns: ["Opponents", ...RECORD_COLUMNS],
          rows: rows.map((row) => [row.label, ...recordCells(row.record)]),
        }}
        title={caption}
      >
        <PointsPerMatchBars
          descriptionId={ids.descId}
          rows={rows}
          titleId={ids.titleId}
        />
      </ChartFigure>
    </div>
  );
}

/** The discipline table: the club's per-match averages beside the league's. */
function DisciplineCard(props: { readonly data: TeamSeasonStats }): ReactElement {
  const { data } = props;
  const rows = [
    {
      label: "Yellow cards",
      own: data.per_match.for.yellow,
      conceded: data.per_match.against.yellow,
      league: data.league_avg.yellow,
    },
    {
      label: "Red cards",
      own: data.per_match.for.red,
      conceded: data.per_match.against.red,
      league: data.league_avg.red,
    },
    {
      label: "Fouls",
      own: data.per_match.for.fouls,
      conceded: data.per_match.against.fouls,
      league: data.league_avg.fouls,
    },
  ];

  return (
    <div className={styles.card}>
      <CardHead label="Discipline per match" />
      <div className={styles.tableWrap}>
        <table className={styles.table}>
          <caption className={styles.caption}>
            Discipline per match, {data.season} season
          </caption>
          <thead>
            <tr>
              <th className={`${styles.cell} ${styles.headCell}`} scope="col">
                Statistic
              </th>
              <th
                className={`${styles.cell} ${styles.headCell} ${styles.numeric}`}
                scope="col"
              >
                For
              </th>
              <th
                className={`${styles.cell} ${styles.headCell} ${styles.numeric}`}
                scope="col"
              >
                Against
              </th>
              <th
                className={`${styles.cell} ${styles.headCell} ${styles.numeric}`}
                scope="col"
              >
                League
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.label}>
                <th className={`${styles.cell} ${styles.rowHeader}`} scope="row">
                  {row.label}
                </th>
                <td className={`${styles.cell} ${styles.numeric}`}>
                  {formatValue(row.own, 2)}
                </td>
                <td className={`${styles.cell} ${styles.numeric}`}>
                  {formatValue(row.conceded, 2)}
                </td>
                <td className={`${styles.cell} ${styles.numeric}`}>
                  {formatValue(row.league, 2)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className={styles.cardNote}>
        Every value is a per-match average over the played matches that carry the
        statistic, so the columns rest on the same kind of denominator but not
        necessarily on the same number of matches. A dash means no counted match
        carries the value.
      </p>
    </div>
  );
}

type CumulativeSeries = {
  /** One label per played match, in the API's own order. */
  readonly xLabels: readonly string[];
  /** Cumulative points, one point per played match. */
  readonly points: readonly number[];
  /** Cumulative market expected points, null until the first priced match. */
  readonly xpts: readonly (number | null)[];
  /** How many played matches carry a market price. */
  readonly pricedMatches: number;
};

/**
 * Sum points and market expected points over the played matches.
 *
 * A match the market did not price adds nothing to the expected-points total:
 * counting it as a zero forecast would invent an expectation nobody published.
 * The cumulative line is therefore null until the first priced match and flat
 * across later gaps. The note below says how much of the season it covers, and,
 * when it is partial, that the two lines then sum different matches.
 */
function cumulativeSeries(
  matches: TeamSeasonStats["matches"],
): CumulativeSeries {
  const xLabels: string[] = [];
  const points: number[] = [];
  const xpts: (number | null)[] = [];
  let pointsTotal = 0;
  let xptsTotal = 0;
  let pricedMatches = 0;

  for (const line of matches) {
    xLabels.push(
      line.match.matchday === null
        ? formatMadridDate(line.match.kickoff_date)
        : `MD${line.match.matchday}`,
    );
    pointsTotal += line.points;
    points.push(pointsTotal);
    if (line.xpts !== null) {
      xptsTotal += line.xpts;
      pricedMatches += 1;
    }
    xpts.push(pricedMatches === 0 ? null : xptsTotal);
  }

  return { xLabels, points, xpts, pricedMatches };
}

/** The cumulative line against the market, with the rolling form behind it. */
function PointsAgainstExpectationCard(props: {
  readonly data: TeamSeasonStats;
}): ReactElement {
  const { data } = props;
  const { xLabels, points, xpts, pricedMatches } = cumulativeSeries(data.matches);

  const cumulative: readonly LineSeries[] = [
    { name: "Points", values: points, emphasis: true },
    { name: "Market xPts", values: xpts },
  ];

  // Partial coverage means the two lines sum different matches: the points line
  // grows on every played match, the market line only on the priced ones. The
  // note says so instead of letting a reader compare a season with its part.
  const coverageCaveat =
    pricedMatches < data.matches.length
      ? " The two lines therefore sum different matches: points earned in a match without a price lift the points line with no published expectation to beat. The comparison is the club's whole season against the priced part of it, and the two lines agree only once every played match is priced."
      : "";

  const priced = formatCount(pricedMatches);
  const played = formatCount(data.matches.length);
  const coverageNote = `Market expected points for ${priced} of ${played} played matches, from the pre-closing price of each one; a match without a price adds nothing to that line, so a gap in it is a gap in the coverage and not a forecast of zero.${coverageCaveat} Above the line = results better than the market expected.`;

  const rolling: readonly LineSeries[] = [
    {
      name: "Points per match",
      values: data.matches.map((line) => line.rolling5.points_pg),
      emphasis: true,
    },
    {
      name: "Market xPts per match",
      values: data.matches.map((line) => line.rolling5.xpts_pg),
    },
    {
      name: "Goals for per match",
      values: data.matches.map((line) => line.rolling5.goals_for_pg),
    },
    {
      name: "Goals against per match",
      values: data.matches.map((line) => line.rolling5.goals_against_pg),
    },
  ];

  return (
    <div className={`${styles.card} ${styles.wideCard}`}>
      <CardHead
        definition="Points the club earned and points the pre-closing odds expected, summed over the played matches in kickoff order. The market line climbs only on a priced match, so it can lag the points line."
        label="Points against expectation"
      />
      <div className={styles.pointsBody}>
        <LineChart
          id="focus-cumulative-points"
          title="Cumulative points against market expectation"
          description={`Points the club earned and points the market expected, summed over the ${formatCount(data.matches.length)} played matches of the ${data.season} season in kickoff order.`}
          xLabels={xLabels}
          yLabel="Points"
          series={cumulative}
          note={coverageNote}
        />
        <dl className={styles.legend}>
          <div className={styles.legendRow}>
            <dt className={styles.legendTerm}>Points won</dt>
            <dd className={styles.legendValue}>{formatCount(data.points)}</dd>
          </div>
          <div className={styles.legendRow}>
            <dt className={styles.legendTerm}>Market expected</dt>
            <dd className={styles.legendValue}>
              {formatValue(data.xpts_market, 1)}
            </dd>
          </div>
        </dl>
      </div>
      <details className={styles.disclosure}>
        <summary className={styles.summary}>Rolling five-match form</summary>
        <LineChart
          id="focus-rolling-form"
          title="Rolling five-match averages"
          description="Points, market expected points and goals for and against, each averaged over the at most five matches the club played on a date strictly earlier than the match the point belongs to. The first matches of the season have no average yet."
          xLabels={xLabels}
          yLabel="Per match"
          series={rolling}
          note="Every series is a per-match average, so the unit of all four lines is per match. A point is missing where the window holds no match, which is the normal state of the first matches of the season."
        />
      </details>
    </div>
  );
}

/** The four cards, for a season with at least one played match. */
function DiagnosisBody(props: { readonly data: TeamSeasonStats }): ReactElement {
  const { data } = props;
  return (
    <div className={styles.cardGrid}>
      <HomeAndAwayCard data={data} />
      <TiersCard data={data} />
      <DisciplineCard data={data} />
      <PointsAgainstExpectationCard data={data} />
    </div>
  );
}

/**
 * The streamed section: the heading is rendered on every path, and the body is
 * decided by the read alone.
 */
export async function DiagnosisSection(
  props: DiagnosisSectionProps,
): Promise<ReactElement> {
  const { teamId, season, seasonOptions } = props;
  const result = await loadSeasonStats(teamId, season);

  let body: ReactElement;
  if (result.ok) {
    body =
      result.data.played === 0 ? (
        <EmptyState title={`No played matches in ${result.data.season}`}>
          <p>
            The club has played no match of this season yet, so there is no
            venue split, no tier split and no points line to diagnose.
          </p>
        </EmptyState>
      ) : (
        <DiagnosisBody data={result.data} />
      );
  } else if (result.status === 404) {
    body = (
      <EmptyState
        title={`No season statistics for ${season}`}
        action={
          <SeasonSelect
            options={seasonOptions}
            value={season}
            path={`/focus/${teamId}`}
          />
        }
      >
        <p>
          The API holds no statistics for this club in this season. Pick another
          season from the list.
        </p>
      </EmptyState>
    );
  } else {
    body = <ProblemState problem={result.problem} />;
  }

  return (
    <section className={styles.section} aria-labelledby="diagnosis-heading">
      <h2 className={styles.heading} id="diagnosis-heading">
        {SECTION_LABEL}
      </h2>
      {body}
    </section>
  );
}
