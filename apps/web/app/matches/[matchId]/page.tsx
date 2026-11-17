/**
 * The match page: one fixture, and what a reader could have known before its
 * kickoff.
 *
 * The page is a preview first. Its probabilities, its two form summaries, its
 * head-to-head record and its two season profiles are the stored or derived
 * pre-kickoff view of the fixture, so a match that has since been played still
 * shows what a reader could have seen before the whistle, next to the result it
 * produced. That is why the probability section is headed `Pre-match view`
 * rather than carrying a claim about now. A row the worker stamped at or after
 * matchday midnight, or one the API withdrew, is named in a sentence of its own
 * rather than drawn, so nothing under that heading claims to predate the
 * kickoff without earning it.
 *
 * Six reads serve it: the fixture (the only source of a played match's
 * statistics), the preview (both form summaries, the record and the stored
 * forecasts), one season-statistics read per club for the profile against the
 * league, and one standings read cut off the day before kickoff for the two
 * header lines. The last three are optional: a club without season statistics
 * loses its profile values, and a table the API refuses loses the header line,
 * never the page.
 *
 * Out of scope on purpose: per-fixture probabilities and the relegation swing a
 * result causes are not M5.
 */
import Link from "next/link";
import { notFound } from "next/navigation";
import type { ReactElement } from "react";

import {
  ChartFigure,
  FormStrip,
  ProbabilityBar,
  chartIds,
} from "@/components/charts";
import { EmptyState, ProblemState } from "@/components/states";
import {
  HowToRead,
  MetricHelp,
  SectionNav,
  type HowToReadItem,
  type SectionNavItem,
} from "@/components/ui";
import { get, type ApiResult, type GetData, type Problem } from "@/lib/api";
import {
  EM_DASH,
  formatCount,
  formatPercent,
  formatPoints,
  formatValue,
} from "@/lib/format";
import { matchStatusLabel, modelLabel, outcomeLabel } from "@/lib/labels";
import { formatMadridDate, formatMadridDateTime } from "@/lib/madrid";

import { MirroredBars, type MirroredRow } from "./MirroredBars";
import styles from "./page.module.css";

export const dynamic = "force-dynamic";

type MatchIdProps = {
  readonly params: Promise<{ matchId: string }>;
};

type Preview = GetData<"/v1/matches/{match_id}/preview">;
type MatchSummary = Preview["match"];
type MatchDetail = GetData<"/v1/matches/{match_id}">;
type SeasonStats = GetData<"/v1/teams/{team_id}/season-stats">;
type Standings = GetData<"/v1/standings">;
type StandingRow = Standings["rows"][number];
type StatLine = SeasonStats["per_match"]["against"];
type Prediction = Preview["predictions"][number];
type Outcome = "H" | "D" | "A";
type Probability = Prediction["probabilities"];

/** The evaluation the API gives a row stamped at or after matchday midnight. */
const LATE_EVALUATION = "late";
const WITHDRAWN_EVALUATION = "withdrawn";

const OUTCOMES: readonly Outcome[] = ["H", "D", "A"];
const RANK_WORDS = ["first", "second", "third"] as const;

/** The short sub-line of each model row, before its as-of date and evaluation. */
const MODEL_SUBLINES: Readonly<Record<Prediction["model"], string>> = {
  dixon_coles: "goals model",
  elo: "team rating",
  base_rate: "baseline · league history only",
  home_win: "baseline · always picks the home side",
  market: "stored forecast from a price",
};

const BASELINE_MODELS: readonly Prediction["model"][] = ["base_rate", "home_win"];

/** One side of a club's per-match line. */
type StatSide = keyof SeasonStats["per_match"];

/** The metrics of the profile, in the order the design lists them. */
const PROFILE_METRICS: readonly {
  readonly label: string;
  readonly key: keyof StatLine;
  readonly side: StatSide;
}[] = [
  { label: "Goals scored", key: "goals", side: "for" },
  { label: "Goals conceded", key: "goals", side: "against" },
  { label: "Shots", key: "shots", side: "for" },
  { label: "Shots on target", key: "shots_on_target", side: "for" },
  { label: "Fouls", key: "fouls", side: "for" },
  { label: "Yellow cards", key: "yellow", side: "for" },
  { label: "Red cards", key: "red", side: "for" },
  { label: "Expected goals", key: "xg", side: "for" },
];

const READING_GUIDE: readonly HowToReadItem[] = [
  {
    term: "Pre-match view",
    definition:
      "Probabilities, form and head-to-head are stored or derived from matches before kickoff, and are never rewritten.",
  },
  {
    term: "Why two baselines",
    definition:
      'A model is only useful if it beats simple rules: "the home side always wins" and "results happen at their historical rates".',
  },
  {
    term: "Market row",
    definition:
      "A pre-closing price with the margin removed, shown only when it is stored; for now that is after the result.",
  },
  {
    term: "Season profile",
    definition: "A season-to-date average, so it is not a pre-kickoff number.",
  },
];

/**
 * Name the stored rows that are not pre-kickoff forecasts.
 *
 * The API marks a row `late` when the worker stamped it at or after matchday
 * midnight. Such a row is not a forecast anyone could have read before the
 * whistle, so it is stated here instead of being drawn next to the pre-kickoff
 * bars, where it would borrow their claim. The evaluation is derived by the API
 * when it reads the row, so the sentence says the stored prediction stays and the
 * API labels it late, not that `late` is a stored column.
 */
function lateNote(late: readonly Prediction[]): string {
  const models = late.map((prediction) => modelLabel(prediction.model)).join(", ");
  return late.length === 1
    ? `${models} was stamped at or after matchday midnight for this fixture, so it is not a pre-kickoff forecast and is not drawn here. The stored prediction remains, and the API labels it late.`
    : `${models} were stamped at or after matchday midnight for this fixture, so they are not pre-kickoff forecasts and are not drawn here. The stored predictions remain, and the API labels them late.`;
}

/**
 * Return the fixture identifier, or `null` for anything that is not one.
 *
 * A non-numeric or non-positive segment names no resource, so the page answers
 * `notFound()` instead of asking the API for a `422` it cannot render.
 */
function parseMatchId(value: string): number | null {
  if (!/^\d+$/.test(value)) {
    return null;
  }
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : null;
}

/** Return the ordinal spelling of a positive integer (`1st`, `2nd`, `11th`). */
function ordinal(value: number): string {
  const remainder = value % 100;
  if (remainder >= 11 && remainder <= 13) {
    return `${value}th`;
  }
  switch (value % 10) {
    case 1:
      return `${value}st`;
    case 2:
      return `${value}nd`;
    case 3:
      return `${value}rd`;
    default:
      return `${value}th`;
  }
}

/**
 * Return the most likely outcome, breaking ties in the H, D, A order.
 *
 * A strict comparison keeps the first outcome of an equal pair, so a tie is
 * always read as the earlier of the two.
 */
function argmax(probabilities: Probability): Outcome {
  const values: Readonly<Record<Outcome, number>> = {
    H: probabilities.home,
    D: probabilities.draw,
    A: probabilities.away,
  };
  let best: Outcome = "H";
  for (const outcome of OUTCOMES) {
    if (values[outcome] > values[best]) {
      best = outcome;
    }
  }
  return best;
}

/** Return the probability a forecast gave one outcome. */
function probabilityOf(probabilities: Probability, outcome: Outcome): number {
  return probabilities[outcome === "H" ? "home" : outcome === "D" ? "draw" : "away"];
}

/**
 * Return the 1-based rank of an outcome in a forecast, ties broken H, D, A.
 */
function outcomeRank(probabilities: Probability, outcome: Outcome): number {
  const order = [...OUTCOMES].sort(
    (left, right) =>
      probabilityOf(probabilities, right) - probabilityOf(probabilities, left) ||
      OUTCOMES.indexOf(left) - OUTCOMES.indexOf(right),
  );
  return order.indexOf(outcome) + 1;
}

/** Return `min–max %` across the rounded percentages, or a single value. */
function percentSpan(values: readonly number[]): string {
  const minimum = Math.min(...values);
  const maximum = Math.max(...values);
  return minimum === maximum ? `${minimum} %` : `${minimum}–${maximum} %`;
}

/** Return `home–away`, with an em dash for a goal that is not known. */
function scoreLine(fixture: MatchSummary): string {
  return `${formatCount(fixture.home_goals)}–${formatCount(fixture.away_goals)}`;
}

/** Return the ISO date one calendar day before `date`, read as UTC. */
function dayBefore(date: string): string {
  const parsed = new Date(`${date}T00:00:00Z`);
  parsed.setUTCDate(parsed.getUTCDate() - 1);
  return parsed.toISOString().slice(0, 10);
}

/**
 * Read an optional resource, treating a refusal and an unreachable API alike.
 *
 * The profile rows and the header standings line are supplementary: losing one
 * of them must not cost the page its fixture, so a thrown read is answered as an
 * absent result here rather than as the page's error.
 */
async function optionalRead<T>(
  read: Promise<ApiResult<T>>,
): Promise<ApiResult<T> | null> {
  try {
    return await read;
  } catch {
    return null;
  }
}

/** The page a handled refusal renders, with the heading still in place. */
function RefusedPage(props: { readonly problem: Problem | null }): ReactElement {
  return (
    <div className={styles.main}>
      <header className={styles.header}>
        <h1 className={styles.title}>Match preview</h1>
      </header>
      <ProblemState problem={props.problem} />
    </div>
  );
}

/** A count with its noun in the right number: "1 match", "12 matches". */
function countPhrase(
  count: number,
  singular: string,
  plural: string = `${singular}s`,
): string {
  return `${formatCount(count)} ${count === 1 ? singular : plural}`;
}

/** The two header lines the standings read can add, or `null` for each. */
function standingLine(row: StandingRow | null): string | null {
  if (row === null || row.played === 0) {
    return null;
  }
  return `${ordinal(row.position)} · ${formatPoints(row.points)} pts after ${countPhrase(
    row.played,
    "match",
  )}`;
}

/**
 * The header's lead sentence: what the main forecast sees, and whether the two
 * models disagree. The tense follows the fixture, because a played match's
 * stored forecast did its seeing before the whistle.
 */
function forecastLead(
  models: readonly Prediction[],
  main: Prediction,
  home: string,
  away: string,
  played: boolean,
): string {
  const plural = models.length > 1;
  const group = plural ? "The models" : modelLabel(main.model);
  const favourite = argmax(main.probabilities);
  const ranked = [...OUTCOMES]
    .map((outcome) => probabilityOf(main.probabilities, outcome))
    .sort((left, right) => right - left);
  const margin = ranked[0] - ranked[1];
  const side =
    favourite === "H" ? home : favourite === "A" ? away : "a draw";

  let sentence: string;
  if (favourite === "D") {
    const verb = played ? "rated" : plural ? "rate" : "rates";
    sentence = `${group} ${verb} a draw as the most likely result.`;
  } else if (margin < 0.05) {
    const verb = played ? "saw" : plural ? "see" : "sees";
    sentence = `${group} ${verb} no clear favourite.`;
  } else if (margin < 0.15) {
    const verb = played ? "leaned" : plural ? "lean" : "leans";
    sentence = `${group} ${verb} towards ${side}, but not by much.`;
  } else {
    const verb = played ? "made" : plural ? "make" : "makes";
    sentence = `${group} ${verb} ${side} the favourite.`;
  }

  const dixonColes = models.find((prediction) => prediction.model === "dixon_coles");
  const elo = models.find((prediction) => prediction.model === "elo");
  if (
    dixonColes !== undefined &&
    elo !== undefined &&
    argmax(dixonColes.probabilities) !== argmax(elo.probabilities)
  ) {
    sentence += played
      ? " Dixon-Coles and Elo disagreed on the most likely result."
      : " Dixon-Coles and Elo disagree on the most likely result.";
  }
  return sentence;
}

/** The header's range sentence across the drawn models, rounded per model. */
function forecastRange(
  models: readonly Prediction[],
  home: string,
  away: string,
): string {
  const spans = (key: keyof Probability): readonly number[] =>
    models.map((prediction) => Math.round(prediction.probabilities[key] * 100));
  return `${home} win ${percentSpan(spans("home"))}, draw ${percentSpan(
    spans("draw"),
  )}, ${away} win ${percentSpan(spans("away"))}.`;
}

/** One model's probability row: label, sub-line and an unlabelled bar. */
function ModelRow(props: { readonly prediction: Prediction }): ReactElement {
  const { prediction } = props;
  const baseline = BASELINE_MODELS.includes(prediction.model);
  const { home, draw, away } = prediction.probabilities;
  return (
    <tr className={baseline ? styles.baselineRow : undefined}>
      <th className={`${styles.cell} ${styles.modelCell}`} scope="row">
        <span className={styles.modelLabel}>{modelLabel(prediction.model)}</span>
        <span className={styles.modelSub}>
          {`${MODEL_SUBLINES[prediction.model]} · as of ${formatMadridDate(
            prediction.as_of_date,
          )} · ${prediction.evaluation}`}
        </span>
        <span className={styles.modelBar} aria-hidden="true">
          <span
            className={styles.modelBarHome}
            style={{ width: `${home * 100}%` }}
          />
          <span
            className={styles.modelBarDraw}
            style={{ width: `${draw * 100}%` }}
          />
          <span
            className={styles.modelBarAway}
            style={{ width: `${away * 100}%` }}
          />
        </span>
      </th>
      <td className={`${styles.cell} ${styles.numericCell}`}>
        {formatPercent(home, 1)}
      </td>
      <td className={`${styles.cell} ${styles.numericCell}`}>
        {formatPercent(draw, 1)}
      </td>
      <td className={`${styles.cell} ${styles.numericCell}`}>
        {formatPercent(away, 1)}
      </td>
    </tr>
  );
}

/**
 * The head-to-head record as a chart: three shares of the meetings, each with
 * its visible label, and the exact counts in the data table.
 */
function RecordBar(props: {
  readonly fixtureId: number;
  readonly home: string;
  readonly away: string;
  readonly record: Preview["head_to_head"];
}): ReactElement {
  const { fixtureId, home, away, record } = props;
  const ids = chartIds(`match-${fixtureId}-head-to-head`);
  const title = "Head-to-head record";
  const description = `${home} ${formatCount(record.home_side_wins)} wins, ${formatCount(
    record.draws,
  )} draws, ${away} ${formatCount(record.away_side_wins)} wins`;
  return (
    <ChartFigure
      ids={ids}
      title={title}
      description={description}
      table={{
        caption: `The ${countPhrase(record.played, "meeting")} of ${home} and ${away} before this fixture, counted from this fixture's home side`,
        columns: ["Record", `${home} wins`, "Draws", `${away} wins`],
        rows: [
          [
            "Meetings",
            formatCount(record.home_side_wins),
            formatCount(record.draws),
            formatCount(record.away_side_wins),
          ],
        ],
      }}
      note="Counted from this fixture's home side, whatever end of the ground each club played at."
    >
      <div
        role="img"
        aria-labelledby={ids.labelledBy}
        className={styles.recordPlot}
      >
        <div className={styles.recordTrack} aria-hidden="true">
          <span
            className={`${styles.recordSegment} ${styles.recordHome}`}
            style={{ flexGrow: record.home_side_wins }}
          />
          <span
            className={`${styles.recordSegment} ${styles.recordDraw}`}
            style={{ flexGrow: record.draws }}
          />
          <span
            className={`${styles.recordSegment} ${styles.recordAway}`}
            style={{ flexGrow: record.away_side_wins }}
          />
        </div>
        <div className={styles.recordLabels} aria-hidden="true">
          <span className={styles.recordLabel}>
            <span className={`${styles.recordSwatch} ${styles.recordHome}`} />
            {`${home} ${formatCount(record.home_side_wins)}`}
          </span>
          <span className={styles.recordLabel}>
            <span className={`${styles.recordSwatch} ${styles.recordDraw}`} />
            {`Draws ${formatCount(record.draws)}`}
          </span>
          <span className={styles.recordLabel}>
            <span className={`${styles.recordSwatch} ${styles.recordAway}`} />
            {`${away} ${formatCount(record.away_side_wins)}`}
          </span>
        </div>
      </div>
    </ChartFigure>
  );
}

/** One side of the profile, or `null` for a club without season statistics. */
function sideValue(
  stats: ApiResult<SeasonStats> | null,
  key: keyof StatLine,
  side: StatSide,
): number | null {
  return stats !== null && stats.ok ? stats.data.per_match[side][key] : null;
}

/** The league average of a metric, from whichever club's read succeeded. */
function leagueValue(
  stats: SeasonStats | null,
  key: keyof StatLine,
): number | null {
  return stats === null ? null : stats.league_avg[key];
}

export default async function MatchPage(
  props: MatchIdProps,
): Promise<ReactElement> {
  const { matchId } = await props.params;
  const id = parseMatchId(matchId);
  if (id === null) {
    notFound();
  }

  const [matchResult, previewResult] = await Promise.all([
    get("/v1/matches/{match_id}", { path: { match_id: id } }),
    get("/v1/matches/{match_id}/preview", { path: { match_id: id } }),
  ]);

  if (!matchResult.ok) {
    if (matchResult.status === 404) {
      notFound();
    }
    return <RefusedPage problem={matchResult.problem} />;
  }
  if (!previewResult.ok) {
    if (previewResult.status === 404) {
      notFound();
    }
    return <RefusedPage problem={previewResult.problem} />;
  }

  const fixture: MatchDetail = matchResult.data;
  const preview: Preview = previewResult.data;
  const season = preview.match.season;

  // The profile is a per-season view of each club, so both reads pass the
  // fixture's season. Neither can fail the page: the section that uses them
  // reports its own gap.
  const [homeStats, awayStats, standings] = await Promise.all([
    optionalRead(
      get("/v1/teams/{team_id}/season-stats", {
        path: { team_id: fixture.home_team.id },
        query: { season },
      }),
    ),
    optionalRead(
      get("/v1/teams/{team_id}/season-stats", {
        path: { team_id: fixture.away_team.id },
        query: { season },
      }),
    ),
    // The boundary is exclusive: a table as it stood before this kickoff
    // counts the matches played on the preceding day, never this fixture.
    optionalRead(
      get("/v1/standings", {
        query: { season, as_of: dayBefore(fixture.kickoff_date) },
      }),
    ),
  ]);

  const home = fixture.home_team.name;
  const away = fixture.away_team.name;
  const played = fixture.status === "played";
  const record = preview.head_to_head;
  const odds = preview.odds;

  const standing = preview.predictions.filter(
    (prediction) =>
      prediction.evaluation !== LATE_EVALUATION &&
      prediction.evaluation !== WITHDRAWN_EVALUATION,
  );
  const late = preview.predictions.filter(
    (prediction) => prediction.evaluation === LATE_EVALUATION,
  );
  // Dixon-Coles leads the group when it is stored, Elo is the fallback.
  const models = [
    ...standing.filter((prediction) => prediction.model === "dixon_coles"),
    ...standing.filter((prediction) => prediction.model === "elo"),
  ];
  const main =
    models.find((prediction) => prediction.model === "dixon_coles") ??
    models.find((prediction) => prediction.model === "elo") ??
    null;

  const standingsRows = standings !== null && standings.ok ? standings.data.rows : null;
  const homeStanding =
    standingsRows === null
      ? null
      : (standingsRows.find((row) => row.team.id === fixture.home_team.id) ?? null);
  const awayStanding =
    standingsRows === null
      ? null
      : (standingsRows.find((row) => row.team.id === fixture.away_team.id) ?? null);

  const sections: readonly SectionNavItem[] = [
    { id: "models", label: "Models" },
    { id: "form", label: "Form" },
    { id: "head-to-head", label: "Head-to-head" },
    { id: "profile", label: "Profile" },
    ...(played ? [{ id: "result", label: "Result" }] : []),
    { id: "how-to-read", label: "How to read" },
  ];

  const homeProfile = homeStats !== null && homeStats.ok ? homeStats.data : null;
  const awayProfile = awayStats !== null && awayStats.ok ? awayStats.data : null;
  const leagueStats = homeProfile ?? awayProfile;
  const bothProfiles = homeProfile !== null && awayProfile !== null;
  const includeXg =
    homeProfile !== null &&
    awayProfile !== null &&
    homeProfile.xg_coverage > 0 &&
    awayProfile.xg_coverage > 0;
  const profileRows: readonly MirroredRow[] = PROFILE_METRICS.filter(
    (metric) => metric.key !== "xg" || includeXg,
  ).map((metric) => ({
    label: metric.label,
    left: sideValue(homeStats, metric.key, metric.side),
    right: sideValue(awayStats, metric.key, metric.side),
    league: leagueValue(leagueStats, metric.key),
  }));

  const missingStatsNotes = [
    homeProfile === null ? `No season statistics for ${home} in ${season}.` : null,
    awayProfile === null ? `No season statistics for ${away} in ${season}.` : null,
  ].filter((note): note is string => note !== null);
  const profileNotes = [
    ...missingStatsNotes,
    includeXg
      ? null
      : "Expected goals is left out: at least one club has no expected-goals coverage this season.",
    "Each row is scaled to its own largest value and the tick marks the league average.",
  ].filter((note): note is string => note !== null);

  const lastMeetings = record.matches;
  const meetingCount = lastMeetings.length;
  const lastMeetingsTable =
    meetingCount === 0 ? null : (
      <div className={styles.tableWrap}>
        <table className={styles.table}>
          <caption className={styles.caption}>
            {`Last ${formatCount(meetingCount)} ${
              meetingCount === 1 ? "meeting" : "meetings"
            }, newest first`}
          </caption>
          <thead>
            <tr>
              <th className={`${styles.cell} ${styles.headCell}`} scope="col">
                Date
              </th>
              <th className={`${styles.cell} ${styles.headCell}`} scope="col">
                Match
              </th>
            </tr>
          </thead>
          <tbody>
            {lastMeetings.map((meeting) => (
              <tr key={meeting.id}>
                <th
                  className={`${styles.cell} ${styles.nowrap}`}
                  scope="row"
                >
                  <Link href={`/matches/${meeting.id}`}>
                    {formatMadridDate(meeting.kickoff_date)}
                  </Link>
                </th>
                <td className={`${styles.cell} ${styles.matchCell}`}>
                  {`${meeting.home_team.name} ${scoreLine(meeting)} ${meeting.away_team.name}`}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );

  return (
    <div className={styles.main}>
      <header className={styles.header}>
        <h1 className={styles.title}>
          <span className={styles.homeName}>{home}</span>
          {" "}
          <span className={styles.vs}>vs</span>
          {" "}
          <span className={styles.awayName}>{away}</span>
        </h1>

        {main === null ? (
          <p className={styles.lead}>
            No stored pre-kickoff forecast from Dixon-Coles or Elo for this
            fixture.
          </p>
        ) : (
          <>
            <p className={styles.lead}>
              <strong>{forecastLead(models, main, home, away, played)}</strong>
            </p>
            <p className={styles.range}>
              {forecastRange(models, home, away)}
            </p>
          </>
        )}

        <div className={styles.headerGrid}>
          <div className={styles.headerSide}>
            <p className={styles.sideLabel}>Home</p>
            {standingLine(homeStanding) === null ? null : (
              <p className={styles.standingLine}>
                {standingLine(homeStanding)}
              </p>
            )}
          </div>

          <div className={styles.headerCentre}>
            {played ? (
              <>
                <p className={styles.score}>{scoreLine(fixture)}</p>
                <p className={styles.centreMeta}>
                  {fixture.result === null ? EM_DASH : outcomeLabel(fixture.result)}
                </p>
                <p className={styles.centreMeta}>
                  {formatMadridDate(fixture.kickoff_date)}
                </p>
              </>
            ) : (
              <>
                <p className={styles.centreMeta}>
                  {formatMadridDate(fixture.kickoff_date)}
                </p>
                {fixture.kickoff_time === null ? null : (
                  <p className={styles.centreMeta}>
                    {formatMadridDateTime(fixture.kickoff_time)}
                  </p>
                )}
                {fixture.matchday === null ? null : (
                  <p className={styles.centreMeta}>
                    <Link href={`/matchday?matchday=${fixture.matchday}`}>
                      {`Matchday ${fixture.matchday}`}
                    </Link>
                  </p>
                )}
                <p className={styles.centreStatus}>
                  {matchStatusLabel(fixture.status)}
                </p>
              </>
            )}
          </div>

          <div className={`${styles.headerSide} ${styles.headerSideAway}`}>
            <p className={styles.sideLabel}>Away</p>
            {standingLine(awayStanding) === null ? null : (
              <p className={styles.standingLine}>
                {standingLine(awayStanding)}
              </p>
            )}
          </div>
        </div>

        {main === null ? null : (
          <ProbabilityBar
            id={`match-${fixture.id}-main`}
            title={`${modelLabel(main.model)} probabilities`}
            home={{ name: home, probability: main.probabilities.home }}
            draw={{ probability: main.probabilities.draw }}
            away={{ name: away, probability: main.probabilities.away }}
            note={`Main forecast: ${modelLabel(main.model)}, as of ${formatMadridDate(
              main.as_of_date,
            )}, stored before kickoff and never rewritten.`}
          />
        )}

        {played && fixture.result !== null && main !== null ? (
          <p className={styles.resultForecast}>
            {`Result: ${outcomeLabel(fixture.result)}. The stored ${modelLabel(
              main.model,
            )} forecast gave it ${formatPercent(
              probabilityOf(main.probabilities, fixture.result),
            )}, its ${
              RANK_WORDS[outcomeRank(main.probabilities, fixture.result) - 1]
            } most likely outcome.`}
          </p>
        ) : null}
      </header>

      <SectionNav sections={sections} />

      <section id="models" className={styles.section}>
        <div className={styles.headingRow}>
          <h2 className={styles.sectionTitle}>
            Pre-match view: what each model says
          </h2>
          <MetricHelp
            label="pre-kickoff probability"
            definition="The stored chance of each outcome as of the date shown, never rewritten."
          />
        </div>

        {late.length === 0 ? null : (
          <p className={styles.note}>{lateNote(late)}</p>
        )}

        {preview.predictions.length === 0 ? (
          <EmptyState title="No predictions stored for this match" command="make predict">
            <p>
              {"No model has a stored forecast for this fixture, so the page has no stored probability to show. The market price below, when there is one, is the bookmaker's, not a model's."}
            </p>
          </EmptyState>
        ) : null}

        {standing.length === 0 && odds === null ? null : (
          <div className={styles.tableWrap}>
            <table className={styles.table}>
              <caption className={styles.caption}>
                Stored pre-kickoff probabilities. Blue home, grey draw, orange
                away.
              </caption>
              <thead>
                <tr>
                  <th className={`${styles.cell} ${styles.headCell}`} scope="col">
                    Model
                  </th>
                  <th
                    className={`${styles.cell} ${styles.headCell} ${styles.numericCell} ${styles.numericHead}`}
                    scope="col"
                  >
                    {home}
                  </th>
                  <th
                    className={`${styles.cell} ${styles.headCell} ${styles.numericCell} ${styles.numericHead}`}
                    scope="col"
                  >
                    Draw
                  </th>
                  <th
                    className={`${styles.cell} ${styles.headCell} ${styles.numericCell} ${styles.numericHead}`}
                    scope="col"
                  >
                    {away}
                  </th>
                </tr>
              </thead>
              <tbody>
                {standing.map((prediction) => (
                  <ModelRow
                    key={prediction.prediction_id}
                    prediction={prediction}
                  />
                ))}
                {odds === null ? null : (
                  <tr>
                    <th className={`${styles.cell} ${styles.modelCell}`} scope="row">
                      <span className={styles.modelLabel}>Market price</span>
                      <span className={styles.modelSub}>
                        pre-closing, margin removed
                      </span>
                    </th>
                    <td className={`${styles.cell} ${styles.numericCell}`}>
                      {formatPercent(odds.implied.home, 1)}
                    </td>
                    <td className={`${styles.cell} ${styles.numericCell}`}>
                      {formatPercent(odds.implied.draw, 1)}
                    </td>
                    <td className={`${styles.cell} ${styles.numericCell}`}>
                      {formatPercent(odds.implied.away, 1)}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}

        {odds === null ? (
          <p className={styles.note}>
            {played
              ? "No pre-closing price is stored for this fixture."
              : "No pre-closing price is stored for this fixture: the current sources publish odds only with results."}
          </p>
        ) : (
          <p className={`${styles.note} ${styles.marketNote}`}>
            {`Pre-closing odds, margin removed, from ${odds.bookmaker}.`}
          </p>
        )}
      </section>

      <div className={styles.cardGrid}>
        <section id="form" className={styles.card}>
          <div className={styles.headingRow}>
            <h2 className={styles.sectionTitle}>Form before kickoff</h2>
            <MetricHelp
              label="form before kickoff"
              definition="The last five played matches strictly before this date, across seasons, newest first. A win is 3 points, a draw 1."
            />
          </div>
          {[
            { key: "home", name: home, form: preview.home_form },
            { key: "away", name: away, form: preview.away_form },
          ].map((side) => (
            <div className={styles.formBlock} key={side.key}>
              <h3 className={styles.subTitle}>{side.name}</h3>
              <FormStrip
                id={`match-${fixture.id}-${side.key}-form`}
                results={side.form.results}
              />
              <p className={styles.note}>
                {`${formatCount(side.form.points)} pts · ${formatCount(
                  side.form.goals_for,
                )}–${formatCount(side.form.goals_against)} goals in ${countPhrase(
                  side.form.matches.length,
                  "match",
                )}.`}
              </p>
            </div>
          ))}
        </section>

        <section id="head-to-head" className={styles.card}>
          <div className={styles.headingRow}>
            <h2 className={styles.sectionTitle}>
              {`Head-to-head · ${countPhrase(record.played, "meeting")}`}
            </h2>
            <MetricHelp
              label="head-to-head record"
              definition="Earlier meetings of the two clubs at either venue, counted from this fixture's home side."
            />
          </div>

          {record.played === 0 ? (
            <EmptyState title="No previous meetings">
              <p>
                The two clubs have not met before this fixture, so there is no
                record to compare.
              </p>
            </EmptyState>
          ) : (
            <>
              <RecordBar
                fixtureId={fixture.id}
                home={home}
                away={away}
                record={record}
              />
              <ul className={styles.meetings}>
                {lastMeetings.slice(0, 3).map((meeting) => (
                  <li key={meeting.id}>
                    <Link href={`/matches/${meeting.id}`}>
                      {formatMadridDate(meeting.kickoff_date)}
                    </Link>
                    {" "}
                    {`${meeting.home_team.name} ${scoreLine(meeting)} ${meeting.away_team.name}`}
                  </li>
                ))}
              </ul>
              {lastMeetingsTable === null ? null : meetingCount > 3 ? (
                <details className={styles.meetingsDetails} open>
                  <summary>{`All ${meetingCount} recent meetings`}</summary>
                  {lastMeetingsTable}
                </details>
              ) : (
                lastMeetingsTable
              )}
              {record.played > meetingCount ? (
                <p className={styles.note}>
                  {`The ${formatCount(meetingCount)} most recent of ${formatCount(
                    record.played,
                  )} meetings are listed.`}
                </p>
              ) : null}
            </>
          )}
        </section>
      </div>

      <section id="profile" className={styles.section}>
        <div className={styles.headingRow}>
          <h2 className={styles.sectionTitle}>
            The two sides per match, against the league average
          </h2>
          <MetricHelp
            label="per-match average"
            definition="The season-to-date average of each side, against the league average drawn as the tick."
          />
        </div>
        <p className={styles.lead}>
          {`Season to date (${season}): on a played fixture this includes later matches, so it is not part of the pre-kickoff view.`}
        </p>
        {!bothProfiles && leagueStats === null ? (
          <EmptyState title="No season statistics for this fixture">
            <p>
              {`The API holds no per-match statistics for ${home} or ${away} in ${season}, so there is no profile to compare with the league average.`}
            </p>
          </EmptyState>
        ) : (
          <MirroredBars
            id={`match-${fixture.id}-profile`}
            title={`${home} and ${away} per-match profile`}
            description={`Per-match averages of ${home} and ${away} over the ${season} season, each against the league average drawn as a tick on both sides.`}
            leftLabel={home}
            rightLabel={away}
            rows={profileRows}
            note={profileNotes.join(" ")}
          />
        )}
      </section>

      {played ? (
        <section id="result" className={styles.section}>
          <h2 className={styles.sectionTitle}>Result</h2>
          <p className={styles.resultLine}>
            <span className={styles.score}>{scoreLine(fixture)}</span>
            <span>
              {fixture.result === null ? EM_DASH : outcomeLabel(fixture.result)}
            </span>
          </p>
          <p className={styles.note}>
            {`Half-time: ${formatCount(fixture.ht_home_goals)}–${formatCount(
              fixture.ht_away_goals,
            )}`}
          </p>
          {fixture.stats === null ? (
            <EmptyState title="No statistics row for this match">
              <p>
                The source published no statistics for this fixture, so there
                are no shots, fouls or cards to show. The result above is
                stored.
              </p>
            </EmptyState>
          ) : (
            <>
              <div className={styles.tableWrap}>
                <table className={styles.table}>
                  <caption className={styles.caption}>
                    {`Match statistics of ${home} and ${away}`}
                  </caption>
                  <thead>
                    <tr>
                      <th className={`${styles.cell} ${styles.headCell}`} scope="col">
                        Metric
                      </th>
                      <th
                        className={`${styles.cell} ${styles.headCell} ${styles.numericCell} ${styles.numericHead}`}
                        scope="col"
                      >
                        {home}
                      </th>
                      <th
                        className={`${styles.cell} ${styles.headCell} ${styles.numericCell} ${styles.numericHead}`}
                        scope="col"
                      >
                        {away}
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {[
                      {
                        label: "Shots",
                        home: formatCount(fixture.stats.home_shots),
                        away: formatCount(fixture.stats.away_shots),
                      },
                      {
                        label: "Shots on target",
                        home: formatCount(fixture.stats.home_shots_on_target),
                        away: formatCount(fixture.stats.away_shots_on_target),
                      },
                      {
                        label: "Fouls",
                        home: formatCount(fixture.stats.home_fouls),
                        away: formatCount(fixture.stats.away_fouls),
                      },
                      {
                        label: "Corners",
                        home: formatCount(fixture.stats.home_corners),
                        away: formatCount(fixture.stats.away_corners),
                      },
                      {
                        label: "Yellow cards",
                        home: formatCount(fixture.stats.home_yellow),
                        away: formatCount(fixture.stats.away_yellow),
                      },
                      {
                        label: "Red cards",
                        home: formatCount(fixture.stats.home_red),
                        away: formatCount(fixture.stats.away_red),
                      },
                      {
                        label: "Expected goals",
                        home: formatValue(fixture.stats.home_xg, 2),
                        away: formatValue(fixture.stats.away_xg, 2),
                      },
                    ].map((row) => (
                      <tr key={row.label}>
                        <th className={styles.cell} scope="row">
                          {row.label}
                        </th>
                        <td className={`${styles.cell} ${styles.numericCell}`}>
                          {row.home}
                        </td>
                        <td className={`${styles.cell} ${styles.numericCell}`}>
                          {row.away}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className={styles.resultHelp}>
                <MetricHelp
                  label="expected goals"
                  definition="The expected goals of each side as the source published them."
                />
              </div>
            </>
          )}
        </section>
      ) : null}

      <div id="how-to-read">
        <HowToRead items={READING_GUIDE} />
      </div>
    </div>
  );
}
