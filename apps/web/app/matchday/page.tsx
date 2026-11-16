/**
 * The matchday page: the forecasts the worker stored for the fixtures of a round.
 *
 * The page opens on the next scheduled matchday, because that is the question a
 * reader arrives with: what does the model say about the round ahead. A
 * `?matchday=` value moves the page to any other round, and the pager walks the
 * season without a season selector. When the next scheduled fixture carries no
 * matchday — or none is scheduled at all — the page falls back to the fixtures
 * of the coming week instead of inventing a round.
 *
 * The bar drawn on a card is the Dixon-Coles forecast, because that is the model
 * the dashboard is built around. A played fixture shows that stored forecast
 * next to the result it earned rather than a new prediction, and the disclosure
 * lists every stored model with the evaluation it earned. A row the worker
 * stamped at or after matchday midnight is marked `late` by the API, which means
 * it was not a pre-kickoff forecast, so it is never drawn as one. Nothing is
 * derived: a fixture with no usable stored forecast renders no bar.
 */
import Link from "next/link";
import type { ReactElement } from "react";

import { ProbabilityBar } from "@/components/charts";
import { EmptyState, ProblemState } from "@/components/states";
import { get, type GetData, type Problem } from "@/lib/api";
import { EM_DASH, formatCount, formatPercent, formatValue } from "@/lib/format";
import { matchStatusLabel, modelLabel, outcomeLabel } from "@/lib/labels";
import {
  MADRID_TIME_ZONE,
  formatMadridDate,
  formatMadridDateTime,
} from "@/lib/madrid";
import { MATCHDAY_RANGE, parseMatchday, type SearchParams } from "@/lib/params";

import styles from "./page.module.css";

export const dynamic = "force-dynamic";

/** The path this page serves, used for the pager links. */
const PAGE_PATH = "/matchday";

/** The model whose forecast leads a card, as the design fixes it. */
const PRIMARY_MODEL = "dixon_coles";

/** The evaluation the API gives a row stamped at or after matchday midnight. */
const LATE_EVALUATION = "late";

/**
 * Why a card draws no probability bar, phrased for the row it actually has.
 *
 * Three states need different words: no stored forecast at all, a stored
 * Dixon-Coles row that is not a pre-kickoff forecast, and stored rows that do not
 * include the model the bar is built on.
 */
function primaryNote(predictions: readonly Prediction[]): string {
  if (predictions.length === 0) {
    return "No pre-kickoff forecast is stored for this fixture.";
  }
  if (!predictions.some((prediction) => prediction.model === PRIMARY_MODEL)) {
    return "No Dixon-Coles forecast is stored for this fixture; the table below lists the models that are.";
  }
  return "The stored Dixon-Coles forecast for this fixture was stamped at or after matchday midnight, so it is not a pre-kickoff forecast and is not drawn.";
}

/** The days the fallback window covers, starting at Madrid today. */
const WINDOW_DAYS = 7;

const FIXTURE_LIMIT = 20;
const WINDOW_FIXTURE_LIMIT = 50;
const PREDICTION_LIMIT = 500;

type Fixture = GetData<"/v1/matches">["items"][number];
type Prediction = GetData<"/v1/predictions">["items"][number];

type MatchdayPageProps = {
  readonly searchParams: Promise<SearchParams>;
};

/**
 * Return the calendar date in Europe/Madrid today, as `YYYY-MM-DD`.
 *
 * `en-CA` formats exactly that way and the zone makes the answer the Madrid day
 * rather than the server's own, which is the day a fixture in Spain falls on.
 *
 * This helper is local on purpose: `lib/madrid.ts` exports the zone and the
 * formatters but no "today", and that file belongs to the shared data layer.
 */
function madridToday(): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: MADRID_TIME_ZONE,
  }).format(new Date());
}

/**
 * Return an ISO date shifted by whole days.
 *
 * The arithmetic runs at UTC noon so a daylight-saving change on the target day
 * cannot move the result to the neighbouring date.
 */
function addDays(date: string, days: number): string {
  const shifted = new Date(`${date}T12:00:00Z`);
  shifted.setUTCDate(shifted.getUTCDate() + days);
  return shifted.toISOString().slice(0, 10);
}

/** The intro paragraph of a resolved matchday. */
function matchdayIntro(matchday: number, season: string | null): string {
  const round =
    season === null
      ? `matchday ${matchday}`
      : `matchday ${matchday} of the ${season} season`;
  return `The stored forecasts for ${round}, with the evaluation each one earned. A row the worker stamped at or after matchday midnight is marked late and is never drawn as a pre-kickoff probability.`;
}

/**
 * The heading of the page, rendered on every path so a reader always knows
 * which page they are on, even when the fixtures below it could not be read.
 */
function PageHeading(props: {
  readonly title: string;
  readonly intro: string;
}): ReactElement {
  return (
    <header className={styles.header}>
      <h1 className={styles.title}>{props.title}</h1>
      <p className={styles.intro}>{props.intro}</p>
    </header>
  );
}

/**
 * The previous and next matchday links.
 *
 * It is rendered above the cards and also when the card list is empty, so a
 * reader is never stranded on a matchday with nothing to show.
 */
function Pager(props: { readonly matchday: number }): ReactElement {
  const { matchday } = props;
  return (
    <nav className={styles.pager} aria-label="Matchday">
      {matchday > MATCHDAY_RANGE.min ? (
        <Link
          className={styles.pagerLink}
          href={`${PAGE_PATH}?matchday=${matchday - 1}`}
        >
          Previous matchday
        </Link>
      ) : null}
      {matchday < MATCHDAY_RANGE.max ? (
        <Link
          className={styles.pagerLink}
          href={`${PAGE_PATH}?matchday=${matchday + 1}`}
        >
          Next matchday
        </Link>
      ) : null}
    </nav>
  );
}

/**
 * The page of a matchday that was resolved but whose reads were refused.
 *
 * The pager stays above the problem, because another matchday is still worth
 * trying and the reader is not stranded on this one.
 */
function MatchdayProblem(props: {
  readonly matchday: number;
  readonly season: string | null;
  readonly problem: Problem | null;
}): ReactElement {
  return (
    <div className={styles.main}>
      <PageHeading
        title={`Matchday ${props.matchday}`}
        intro={matchdayIntro(props.matchday, props.season)}
      />
      <Pager matchday={props.matchday} />
      <ProblemState problem={props.problem} />
    </div>
  );
}

/** The page of the coming-week fallback whose reads were refused. */
function WindowProblem(props: { readonly problem: Problem | null }): ReactElement {
  return (
    <div className={styles.main}>
      <PageHeading
        title="Upcoming fixtures"
        intro="The fixtures of the coming days, with the forecasts the worker stored for them."
      />
      <ProblemState problem={props.problem} />
    </div>
  );
}

/**
 * One fixture, its stored forecasts and their evaluation.
 *
 * The bar is the Dixon-Coles forecast when the fixture has a stored row that is
 * a pre-kickoff forecast. A row the API marks `late` was stamped at or after
 * matchday midnight, so drawing it here would present a non-pre-kickoff number as
 * the forecast of the round. The card then says why it draws no bar instead of
 * substituting another model or a zero, which would read as a forecast of zero
 * probabilities.
 */
function FixtureCard(props: {
  readonly fixture: Fixture;
  readonly predictions: readonly Prediction[];
}): ReactElement {
  const { fixture, predictions } = props;
  const played = fixture.status === "played";
  const primary: Prediction | undefined = predictions.find(
    (prediction) =>
      prediction.model === PRIMARY_MODEL && prediction.evaluation !== LATE_EVALUATION,
  );
  const home = fixture.home_team.name;
  const away = fixture.away_team.name;
  const score = `${formatCount(fixture.home_goals)}–${formatCount(fixture.away_goals)}`;
  const missing = primaryNote(predictions);

  return (
    <article className={styles.card}>
      <h2 className={styles.cardTitle}>
        <Link href={`/matches/${fixture.id}`}>{`${home} vs ${away}`}</Link>
      </h2>
      <p className={styles.meta}>
        <span>{formatMadridDate(fixture.kickoff_date)}</span>
        <span>{`${formatMadridDateTime(fixture.kickoff_time)} source local time`}</span>
        <span>{matchStatusLabel(fixture.status)}</span>
      </p>
      {played ? (
        <p className={styles.result}>
          <span className={styles.score}>{score}</span>
          <span>
            {fixture.result === null ? EM_DASH : outcomeLabel(fixture.result)}
          </span>
        </p>
      ) : null}
      {primary === undefined ? (
        <p className={styles.note}>{missing}</p>
      ) : (
        <ProbabilityBar
          id={`match-${fixture.id}-${primary.model}`}
          title={`${modelLabel(primary.model)} probabilities`}
          home={{ name: home, probability: primary.probabilities.home }}
          draw={{ probability: primary.probabilities.draw }}
          away={{ name: away, probability: primary.probabilities.away }}
          note={`Stored forecast, evaluation: ${primary.evaluation}.`}
        />
      )}
      {predictions.length === 0 ? null : (
        <details className={styles.disclosure}>
          <summary className={styles.summary}>Every model</summary>
          <div className={styles.tableWrap}>
            <table className={styles.table}>
              <caption className={styles.caption}>
                {`Every stored forecast of ${home} vs ${away}`}
              </caption>
              <thead>
                <tr>
                  <th
                    className={`${styles.cell} ${styles.headCell} ${styles.stickyColumn}`}
                    scope="col"
                  >
                    Model
                  </th>
                  <th className={`${styles.cell} ${styles.headCell}`} scope="col">
                    Home
                  </th>
                  <th className={`${styles.cell} ${styles.headCell}`} scope="col">
                    Draw
                  </th>
                  <th className={`${styles.cell} ${styles.headCell}`} scope="col">
                    Away
                  </th>
                  <th className={`${styles.cell} ${styles.headCell}`} scope="col">
                    Evaluation
                  </th>
                  {played ? (
                    <>
                      <th
                        className={`${styles.cell} ${styles.headCell}`}
                        scope="col"
                      >
                        Correct
                      </th>
                      <th
                        className={`${styles.cell} ${styles.headCell}`}
                        scope="col"
                      >
                        RPS
                      </th>
                    </>
                  ) : null}
                </tr>
              </thead>
              <tbody>
                {predictions.map((prediction) => (
                  <tr key={prediction.id}>
                    <th
                      className={`${styles.cell} ${styles.stickyColumn} ${styles.modelCell}`}
                      scope="row"
                    >
                      {modelLabel(prediction.model)}
                    </th>
                    <td className={styles.cell}>
                      {formatPercent(prediction.probabilities.home)}
                    </td>
                    <td className={styles.cell}>
                      {formatPercent(prediction.probabilities.draw)}
                    </td>
                    <td className={styles.cell}>
                      {formatPercent(prediction.probabilities.away)}
                    </td>
                    <td className={styles.cell}>
                      <span className={styles.badge}>{prediction.evaluation}</span>
                    </td>
                    {played ? (
                      <>
                        <td className={styles.cell}>
                          {prediction.score === null
                            ? EM_DASH
                            : prediction.score.correct
                              ? "Yes"
                              : "No"}
                        </td>
                        <td className={styles.cell}>
                          {formatValue(prediction.score?.rps ?? null, 3)}
                        </td>
                      </>
                    ) : null}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
    </article>
  );
}

/** The card grid, one card per fixture in the order the API returned them. */
function Cards(props: {
  readonly fixtures: readonly Fixture[];
  readonly predictions: readonly Prediction[];
}): ReactElement {
  const { fixtures, predictions } = props;
  return (
    <div className={styles.cards}>
      {fixtures.map((fixture) => (
        <FixtureCard
          key={fixture.id}
          fixture={fixture}
          predictions={predictions.filter(
            (prediction) => prediction.match.id === fixture.id,
          )}
        />
      ))}
    </div>
  );
}

export default async function MatchdayPage(
  props: MatchdayPageProps,
): Promise<ReactElement> {
  const params = await props.searchParams;
  const requested = parseMatchday(params.matchday);
  const today = madridToday();

  // The next scheduled fixture answers two questions at once: which matchday is
  // ahead, and which season it belongs to. A `date_from` of Madrid today skips
  // a postponed fixture, which keeps its past date and its `scheduled` status.
  const nextFixtureResult = await get("/v1/matches", {
    query: { status: "scheduled", date_from: today, order: "asc", limit: 1 },
  });

  // No season filter is sent, so a `404` is impossible here: any refusal is the
  // API declining the read, which is reported instead of drawn.
  if (!nextFixtureResult.ok) {
    return (
      <div className={styles.main}>
        <PageHeading
          title="Matchday"
          intro="The forecasts of the next matchday."
        />
        <ProblemState problem={nextFixtureResult.problem} />
      </div>
    );
  }

  const nextFixture: Fixture | undefined = nextFixtureResult.data.items[0];
  const season: string | null = nextFixture?.season ?? null;
  const nextMatchday: number | null = nextFixture?.matchday ?? null;
  const matchday: number | null = requested ?? nextMatchday;

  if (matchday === null) {
    // No matchday to open: either nothing is scheduled, or the next fixture
    // carries no round. The coming week is the honest answer, because a reader
    // asking about the matchday ahead still wants the fixtures ahead.
    const until = addDays(today, WINDOW_DAYS);
    const [windowMatches, windowPredictions] = await Promise.all([
      get("/v1/matches", {
        query: {
          status: "scheduled",
          date_from: today,
          date_to: until,
          order: "asc",
          limit: WINDOW_FIXTURE_LIMIT,
        },
      }),
      get("/v1/predictions", {
        query: {
          date_from: today,
          date_to: until,
          latest_only: true,
          limit: PREDICTION_LIMIT,
        },
      }),
    ]);

    if (!windowMatches.ok) {
      return <WindowProblem problem={windowMatches.problem} />;
    }

    const fixtures = windowMatches.data.items;

    // A `404` from the forecasts means no season holds a match yet, which is
    // the empty state of the page and not a refusal; any other failure is
    // reported, because leaving the bars out would understate what exists.
    if (!windowPredictions.ok && windowPredictions.status !== 404) {
      return <WindowProblem problem={windowPredictions.problem} />;
    }
    const predictions = windowPredictions.ok
      ? windowPredictions.data.items
      : [];

    return (
      <div className={styles.main}>
        <PageHeading
          title="Upcoming fixtures"
          intro={
            season === null
              ? "The fixtures of the coming days, with the forecasts the worker stored for them."
              : `The fixtures of the coming days of the ${season} season, with the forecasts the worker stored for them.`
          }
        />
        {fixtures.length === 0 ? (
          <EmptyState title="No upcoming fixtures" command="make ingest">
            <p>
              The API lists no scheduled fixture for the coming week, so there is
              no forecast to show yet.
            </p>
          </EmptyState>
        ) : (
          <Cards fixtures={fixtures} predictions={predictions} />
        )}
      </div>
    );
  }

  // An explicit matchday on a stack whose season has no scheduled fixture left
  // has no season yet. The default season of the standings is the one the rest
  // of the dashboard opens on, so the page opens the same one.
  let resolvedSeason: string | null = season;
  if (resolvedSeason === null) {
    const standingsResult = await get("/v1/standings");
    // A `404` there means no season has a match, so the matchday cannot exist.
    resolvedSeason = standingsResult.ok ? standingsResult.data.season : null;
  }

  if (resolvedSeason === null) {
    return (
      <div className={styles.main}>
        <PageHeading
          title={`Matchday ${matchday}`}
          intro={matchdayIntro(matchday, season)}
        />
        <Pager matchday={matchday} />
        <EmptyState title="No upcoming fixtures" command="make ingest">
          <p>
            No season holds a fixture yet, so this matchday has nothing to show.
          </p>
        </EmptyState>
      </div>
    );
  }

  const [matchesResult, predictionsResult] = await Promise.all([
    get("/v1/matches", {
      query: { season: resolvedSeason, matchday, limit: FIXTURE_LIMIT },
    }),
    get("/v1/predictions", {
      query: {
        season: resolvedSeason,
        matchday,
        latest_only: true,
        limit: PREDICTION_LIMIT,
      },
    }),
  ]);

  // A season code that came from the API is known, so a `404` here would mean
  // the two reads disagree about the season.
  if (!matchesResult.ok) {
    return (
      <MatchdayProblem
        matchday={matchday}
        season={resolvedSeason}
        problem={matchesResult.problem}
      />
    );
  }
  if (!predictionsResult.ok) {
    return (
      <MatchdayProblem
        matchday={matchday}
        season={resolvedSeason}
        problem={predictionsResult.problem}
      />
    );
  }

  const fixtures = matchesResult.data.items;
  const predictions = predictionsResult.data.items;

  return (
    <div className={styles.main}>
      <PageHeading
        title={`Matchday ${matchday}`}
        intro={matchdayIntro(matchday, resolvedSeason)}
      />
      <Pager matchday={matchday} />
      {fixtures.length === 0 ? (
        <EmptyState
          title={`No fixtures on matchday ${matchday}`}
          command="make ingest"
        >
          <p>
            The API lists no fixture for this matchday of the {resolvedSeason}{" "}
            season.
          </p>
        </EmptyState>
      ) : (
        <>
          {predictions.length === 0 ? (
            <EmptyState
              title="No predictions stored for this matchday"
              command="make predict"
            >
              <p>
                The fixtures are loaded, but no pre-kickoff forecast is stored
                for them, so the cards below carry no probability bar.
              </p>
            </EmptyState>
          ) : null}
          <Cards fixtures={fixtures} predictions={predictions} />
        </>
      )}
    </div>
  );
}
