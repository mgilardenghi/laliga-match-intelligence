/**
 * "The race at a glance": the sentences a reader can skim before the table.
 *
 * The cards are built from the latest run and the standings the API computed:
 * every chance is read as stored, never re-derived, and the qualification
 * counts come from the run's own threshold instead of a literal. The thresholds
 * that turn a chance into a sentence are this page's editorial rule, so they are
 * named below and used nowhere else.
 *
 * The block renders on the server: no state, no client fetch, no link. A skimmer
 * reads one short sentence per card, and no sentence claims a cause the stored
 * numbers do not carry: a chance counts simulated seasons, it is not a verdict
 * on a squad.
 */
import type { ReactElement, ReactNode } from "react";

import type { GetData } from "@/lib/api";

import {
  joinNames,
  ordinal,
  pct0,
  rankByChance,
  type ProjectedRow,
} from "./derive";
import styles from "../page.module.css";

/** A club is in the title race from a one-in-a-hundred chance upwards. */
const TITLE_RACE_MIN = 0.01;

/** A club is a Champions League certainty from this chance upwards. */
const UCL_SURE_MIN = 0.95;

/** Below this chance a club is out of the Champions League fight entirely. */
const UCL_CONTENDING_MIN = 0.05;

/** A club is in the relegation fight from a one-in-ten chance upwards. */
const RELEGATION_FIGHT_MIN = 0.1;

/** A club outside the drop zone needs this chance to earn the second sentence. */
const RELEGATION_OFF_ZONE_MIN = 0.25;

/** The title and Champions League cards name their three likeliest clubs. */
const SHORT_LIST = 3;

/** The relegation card names four, then counts the rest. */
const RELEGATION_LIST = 4;

/** The three printed thresholds, written once for the sentences that quote them. */
const TITLE_THRESHOLD = "1%";
const UCL_THRESHOLD = "95%";
const RELEGATION_THRESHOLD = "10%";

/** Which colour the card's label carries, and nothing else. */
type GlanceTone = "title" | "ucl" | "relegation";

/** One listed club: the bar segment and the sentence share it. */
type GlanceItem = {
  readonly id: number;
  readonly name: string;
  readonly chance: number;
};

/**
 * The segment classes in the order the bars paint them.
 *
 * A bar has at most three segments because every card lists at most three
 * clubs, so the second and third chances never need a fourth tone.
 */
const SEGMENT_CLASSES = [
  styles.glanceSegmentPrimary,
  styles.glanceSegmentSecondary,
  styles.glanceSegmentTertiary,
] as const;

/** Read the clubs of a list into the shape both the sentence and bar need. */
function glanceItems(
  rows: readonly ProjectedRow[],
  chance: (row: ProjectedRow) => number,
): GlanceItem[] {
  return rows.map((row) => ({
    id: row.team.id,
    name: row.team.name,
    chance: chance(row),
  }));
}

/**
 * Print the tail of a "more clubs" sentence.
 *
 * The count is spelled out so a sentence of one club stays grammatical:
 * "1 more club reaches 1%." instead of "1 more clubs reach 1%.".
 */
function moreClubs(count: number, threshold: string): string {
  return count === 1
    ? `1 more club reaches ${threshold}.`
    : `${count} more clubs reach ${threshold}.`;
}

/**
 * Print a ranked list as one sentence: the leader bold, the rest plain.
 *
 * The bold leader is the club the card is about; the plain tail is the pack
 * behind it, and the sentence always closes with a full stop.
 */
function RankedSentence(props: {
  readonly items: readonly GlanceItem[];
}): ReactElement {
  const [first, ...rest] = props.items;
  const tail = rest
    .map((item) => `, ${item.name} ${pct0(item.chance)}`)
    .join("");
  return (
    <>
      <strong>{`${first.name} ${pct0(first.chance)}`}</strong>
      {`${tail}.`}
    </>
  );
}

/**
 * One card: a label, the sentences the caller passes, and the bar of the clubs
 * the sentences list.
 *
 * The bar is decoration of the sentence beside it, so it is hidden from a screen
 * reader, and it is omitted when no club qualifies: an empty track would suggest
 * a zero chance the card never claimed.
 */
function GlanceCard(props: {
  readonly tone: GlanceTone;
  readonly label: string;
  readonly segments?: readonly GlanceItem[];
  readonly children: ReactNode;
}): ReactElement {
  const { tone, label, segments, children } = props;
  return (
    <article className={styles.glanceCard} data-tone={tone}>
      <h3 className={styles.glanceLabel}>{label}</h3>
      {children}
      {segments === undefined || segments.length === 0 ? null : (
        <div className={styles.glanceBar} aria-hidden="true">
          {segments.map((item, index) => (
            <span
              className={SEGMENT_CLASSES[index]}
              key={item.id}
              style={{
                width: `${Math.min(100, Math.max(0, item.chance * 100))}%`,
              }}
            />
          ))}
        </div>
      )}
    </article>
  );
}

/** The title card: who wins the simulated seasons. */
function TitleCard(props: {
  readonly rows: readonly ProjectedRow[];
}): ReactElement {
  const inRace = rankByChance(
    props.rows.filter((row) => row.p_title >= TITLE_RACE_MIN),
    (row) => row.p_title,
  );
  const items = glanceItems(inRace.slice(0, SHORT_LIST), (row) => row.p_title);
  // Past the three named clubs the card counts the rest instead of growing a
  // list too long to skim.
  const tail =
    inRace.length <= SHORT_LIST
      ? `Nobody else reaches ${TITLE_THRESHOLD}.`
      : moreClubs(inRace.length - SHORT_LIST, TITLE_THRESHOLD);
  return (
    <GlanceCard label="Title" segments={items} tone="title">
      <p className={styles.glanceText}>
        {items.length === 0 ? (
          `No club wins the title in ${TITLE_THRESHOLD} or more of simulated seasons.`
        ) : (
          <>
            <RankedSentence items={items} />
            {` ${tail}`}
          </>
        )}
      </p>
    </GlanceCard>
  );
}

/** The Champions League card: who is sure, and who is still playing for it. */
function ChampionsLeagueCard(props: {
  readonly rows: readonly ProjectedRow[];
  readonly ucl: number;
}): ReactElement {
  const { rows, ucl } = props;
  // The certain places stay in the run's own projected order, so the sentence
  // reads like the table above it; only the fight behind them is ranked by
  // chance, because those clubs are not ordered by anything else.
  const sure = rows.filter((row) => row.p_ucl >= UCL_SURE_MIN);
  const sureSentence =
    sure.length === 0
      ? null
      : `${joinNames(sure.map((row) => row.team.name))} ${
          sure.length === 1 ? "reaches" : "reach"
        } the top ${ucl} in at least ${UCL_THRESHOLD} of seasons.`;
  const contending = rankByChance(
    rows.filter(
      (row) => row.p_ucl >= UCL_CONTENDING_MIN && row.p_ucl < UCL_SURE_MIN,
    ),
    (row) => row.p_ucl,
  ).slice(0, SHORT_LIST);
  const items = glanceItems(contending, (row) => row.p_ucl);
  const hasSentence = sureSentence !== null || items.length > 0;
  return (
    <GlanceCard
      label={`Champions League (top ${ucl})`}
      segments={items}
      tone="ucl"
    >
      {hasSentence ? (
        <p className={styles.glanceText}>
          {sureSentence}
          {items.length === 0 ? null : (
            <>
              {/* Two clauses, one space between them: a card that only has
                  contenders does not open with a blank. */}
              {sureSentence === null ? null : " "}
              {"Contending: "}
              <RankedSentence items={items} />
            </>
          )}
        </p>
      ) : null}
    </GlanceCard>
  );
}

/**
 * Name the club the table has above the drop zone but the simulation sends down.
 *
 * The club is the likeliest relegation of the clubs whose standings position is
 * outside the bottom `relegation` places, and only from a quarter of the
 * simulated seasons upwards: below that, "outside the drop zone" is the story
 * and the note would be noise. The sentence stops at what the numbers say; why
 * the simulation disagrees with the table is not stored with this run.
 */
function offZoneNote(options: {
  readonly rows: readonly ProjectedRow[];
  readonly standings: GetData<"/v1/standings">;
  readonly relegation: number;
}): string | null {
  const positions = new Map<number, number>();
  for (const row of options.standings.rows) {
    positions.set(row.team.id, row.position);
  }
  const lastSafePosition = options.standings.rows.length - options.relegation;
  for (const row of rankByChance(options.rows, (entry) => entry.p_relegation)) {
    const position = positions.get(row.team.id);
    if (
      position !== undefined &&
      position <= lastSafePosition &&
      row.p_relegation >= RELEGATION_OFF_ZONE_MIN
    ) {
      return `${row.team.name} is ${ordinal(position)} in the table but goes down in ${pct0(
        row.p_relegation,
      )} of simulated seasons.`;
    }
  }
  return null;
}

/** The relegation card: who goes down, with no bar to dress it up. */
function RelegationCard(props: {
  readonly rows: readonly ProjectedRow[];
  readonly standings: GetData<"/v1/standings">;
  readonly relegation: number;
}): ReactElement {
  const { rows, standings, relegation } = props;
  const inFight = rankByChance(
    rows.filter((row) => row.p_relegation >= RELEGATION_FIGHT_MIN),
    (row) => row.p_relegation,
  );
  const items = glanceItems(
    inFight.slice(0, RELEGATION_LIST),
    (row) => row.p_relegation,
  );
  const note = offZoneNote({ rows, standings, relegation });
  return (
    <GlanceCard label={`Relegation (bottom ${relegation})`} tone="relegation">
      <p className={styles.glanceText}>
        {items.length === 0 ? (
          `No club goes down in ${RELEGATION_THRESHOLD} or more of simulated seasons.`
        ) : (
          <>
            <RankedSentence items={items} />
            {inFight.length <= RELEGATION_LIST
              ? null
              : ` ${moreClubs(
                  inFight.length - RELEGATION_LIST,
                  RELEGATION_THRESHOLD,
                )}`}
          </>
        )}
      </p>
      {note === null ? null : <p className={styles.glanceNote}>{note}</p>}
    </GlanceCard>
  );
}

export type RaceGlanceProps = {
  readonly projection: GetData<"/v1/simulations/latest">;
  /** The standings the table draws: the club positions the note reads. */
  readonly standings: GetData<"/v1/standings">;
};

/**
 * The "Race at a glance" section of the home page.
 *
 * It exists only with a run, and a card disappears when the run stores no place
 * for it: a season with no Champions League place has no Champions League card,
 * and a season with no relegation has no relegation card.
 */
export function RaceGlance(props: RaceGlanceProps): ReactElement {
  const { projection, standings } = props;
  const rows = projection.table;
  const qualification = projection.run.qualification;
  const ucl = qualification.ucl_positions;
  const relegation = qualification.relegation_positions;
  return (
    <section
      aria-labelledby="race-heading"
      className={`${styles.anchor} ${styles.section}`}
      id="race"
    >
      <h2 className={styles.sectionTitle} id="race-heading">
        The race at a glance
      </h2>
      <div className={styles.glanceGrid}>
        <TitleCard rows={rows} />
        {ucl === 0 ? null : <ChampionsLeagueCard rows={rows} ucl={ucl} />}
        {relegation === 0 ? null : (
          <RelegationCard
            relegation={relegation}
            rows={rows}
            standings={standings}
          />
        )}
      </div>
    </section>
  );
}
