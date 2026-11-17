/**
 * The story card: one sentence about the season and the four numbers behind it.
 *
 * The card is the page's opening answer, so it reads the same facts the sections
 * below explain: the table row the focus payload already holds, the season
 * statistics, and, when a run is stored, that run's qualification counts. The
 * sentence is `story.ts`'s, not this file's, because the thresholds belong to a
 * rule rather than to a layout.
 *
 * Every tile is a shared `StatTile` with its own definition, and a number the
 * payload does not carry is an em dash rather than a zero: a season with no
 * simulation must not read as a season of zero risk.
 */
import type { ReactElement } from "react";

import { ProblemState } from "@/components/states";
import { StatTile, type StatTone } from "@/components/ui";
import { EM_DASH, formatCount, formatPercent, formatPoints, formatSigned } from "@/lib/format";

import {
  loadSeasonStats,
  loadSimulationDetail,
  type TeamFocus,
  type TeamSeasonStats,
} from "./data";
import {
  chanceTitle,
  chanceValue,
  marketGap,
  marketIsComparable,
  ordinal,
  qualificationLabels,
  storyBand,
  storySentence,
  type Qualification,
  type StoryBand,
} from "./story";
import styles from "./page.module.css";

/** The label the `<Suspense>` boundary of this card shows while it reads. */
export const SECTION_LABEL = "The story of this season";

export type StorySectionProps = {
  readonly teamId: number;
  /** The season the page resolved, never the raw query value. */
  readonly season: string;
  readonly standing: TeamFocus["standing"];
  readonly splits: TeamFocus["splits"];
  readonly outlook: TeamFocus["outlook"];
};

/** What the position tile shows under its value. */
function positionSubline(standing: TeamFocus["standing"]): string {
  if (standing === null) {
    return "No table row stored";
  }
  return `${formatCount(standing.points)} pts · GD ${formatSigned(
    standing.goal_difference,
  )} · ${formatCount(standing.played)} played`;
}

/** The value of the points-against-market tile. */
function marketValue(
  stats: TeamSeasonStats | null,
): { readonly value: string; readonly subline: string; readonly tone: StatTone } {
  if (stats === null || !marketIsComparable(stats)) {
    return {
      value: EM_DASH,
      subline:
        stats === null
          ? "No season statistics stored"
          : `Market prices for ${formatCount(stats.xpts_coverage)} of ${formatCount(stats.played)} matches`,
      tone: "neutral",
    };
  }
  const gap = marketGap(stats);
  if (gap === null) {
    return { value: EM_DASH, subline: "No market price stored", tone: "neutral" };
  }
  return {
    value: formatSigned(gap),
    subline: `${formatPoints(stats.points)} won, ${formatPoints(stats.xpts_market)} expected`,
    tone: gap <= -1 ? "bad" : gap >= 1 ? "good" : "neutral",
  };
}

/**
 * The chance tile: the band's own probability, with the other three under it.
 *
 * The three companions are the answer to "and the rest of the season?", and
 * their names carry the stored qualification counts so the card says "top 4"
 * only when the run was asked for four.
 */
function chanceTile(
  band: StoryBand,
  outlook: TeamFocus["outlook"],
  qualification: Qualification | null,
): { readonly definition: string; readonly subline: string } {
  const labels = qualificationLabels(qualification);
  if (outlook === null) {
    return {
      definition:
        "The share of the stored simulation runs in which the club finishes in the position this tile names, from the run that produced the season's outlook.",
      subline: "No simulation stored",
    };
  }
  const others: string[] = [];
  if (band.kind !== "title") {
    others.push(`title ${formatPercent(outlook.p_title)}`);
  }
  if (band.kind !== "ucl") {
    others.push(`${labels.ucl} ${formatPercent(outlook.p_ucl)}`);
  }
  if (band.kind !== "europe") {
    others.push(`${labels.europe} ${formatPercent(outlook.p_europe)}`);
  }
  if (
    band.kind !== "relegation" &&
    band.kind !== "between" &&
    band.kind !== "none"
  ) {
    others.push(`${labels.relegation} ${formatPercent(outlook.p_relegation)}`);
  }
  return {
    definition:
      "The share of the stored simulation runs of this season in which the club finished in the position this tile names. Every remaining fixture is simulated from the stored model probabilities, so the chance is a measurement of those runs and not a forecast rule.",
    subline: others.join(" · "),
  };
}

/** The definition of the tile that compares the points with the market. */
const MARKET_HELP =
  "For each played match, the pre-closing 1x2 betting odds give a win, a draw and a loss chance, and 3 × the win chance plus the draw chance is what the market thought that match was worth. This tile subtracts the season's sum of those expectations from the points the club actually won, so a positive value is a club beating the price.";

/** The definition of the expected-points tile. */
const EXPECTED_POINTS_HELP =
  "The mean final points total over the stored simulation runs of the season, counting the matches already played as results and simulating every remaining fixture. The interval below it is the 5th to 95th percentile of the same runs.";

/** The tile grid of the story card. */
function StoryTiles(props: {
  readonly standing: TeamFocus["standing"];
  readonly stats: TeamSeasonStats | null;
  readonly outlook: TeamFocus["outlook"];
  readonly band: StoryBand;
  readonly qualification: Qualification | null;
}): ReactElement {
  const { standing, stats, outlook, band, qualification } = props;
  const market = marketValue(stats);
  const chance = chanceTile(band, outlook, qualification);
  const relegation = band.kind === "relegation" || band.kind === "between";

  return (
    <div className={styles.storyTiles}>
      <StatTile
        help={{
          definition:
            "This club's place in the season table, ranked by points and then by goal difference over the matches already played.",
          direction: "lower-is-better",
        }}
        label="Position"
        subline={positionSubline(standing)}
        value={standing === null ? EM_DASH : ordinal(standing.position)}
      />
      <StatTile
        help={{ definition: MARKET_HELP, direction: "higher-is-better" }}
        label="Points vs market"
        subline={market.subline}
        tone={market.tone}
        value={market.value}
      />
      <StatTile
        help={{ definition: EXPECTED_POINTS_HELP, direction: "higher-is-better" }}
        label="Expected final points"
        subline={
          outlook === null
            ? "No simulation stored"
            : `90 % of simulations between ${formatCount(
                outlook.points_p05,
              )} and ${formatCount(outlook.points_p95)}`
        }
        value={outlook === null ? EM_DASH : formatPoints(outlook.expected_points)}
      />
      <StatTile
        help={{
          definition: chance.definition,
          direction: relegation ? "lower-is-better" : "higher-is-better",
        }}
        label={chanceTitle(band)}
        subline={chance.subline}
        value={formatPercent(chanceValue(band, outlook))}
      />
    </div>
  );
}

/**
 * The streamed card: the sentence is the heading, so the section's accessible
 * name says what the season is about before a reader reaches a tile.
 */
export async function StorySection(
  props: StorySectionProps,
): Promise<ReactElement> {
  const { teamId, season, standing, splits, outlook } = props;

  const statsResult = await loadSeasonStats(teamId, season);
  // The place counts come from the run the outlook names, never from the
  // season's latest one: a run that succeeded between two reads would otherwise
  // pair this outlook with another run's rules.
  const simulationResult =
    outlook === null
      ? null
      : await loadSimulationDetail(outlook.run_id);
  const qualification =
    simulationResult !== null && simulationResult.ok
      ? simulationResult.data.run.qualification
      : null;

  if (!statsResult.ok && statsResult.status !== 404) {
    return (
      <section className={styles.storyCard} aria-labelledby="story-heading">
        <h2 className={styles.storyHeading} id="story-heading">
          {SECTION_LABEL}
        </h2>
        <ProblemState problem={statsResult.problem} />
      </section>
    );
  }

  const stats = statsResult.ok ? statsResult.data : null;
  const band = storyBand(outlook, qualification);
  const sentence = storySentence({
    band,
    standing,
    stats,
    splits,
    season,
  });

  return (
    <section className={styles.storyCard} aria-labelledby="story-heading">
      <h2 className={styles.storyHeading} id="story-heading">
        {sentence}
      </h2>
      <StoryTiles
        band={band}
        outlook={outlook}
        qualification={qualification}
        standing={standing}
        stats={stats}
      />
    </section>
  );
}
