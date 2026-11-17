# ADR-0006: Dashboard reading rules

Status: accepted (2026-11-17)

## Context

The 2026-11 page wave (table, match, focus and performance pages) writes
sentences and picks one number out of many. Each choice has a threshold or an
order that a reader cannot see in the page and that a later change could move
without noticing. The rules live in `apps/web/app/performance/metrics.ts`,
`apps/web/app/focus/[teamId]/story.ts`, `apps/web/app/focus/[teamId]/next-match-section.tsx`
and `apps/web/app/_table/projection-section.tsx`. This record states them once.

## Decision

### The headline model is Dixon-Coles, then Elo, never a baseline

Where a page shows one forecast, it shows the first stored of Dixon-Coles, then
Elo (`MODEL_ORDER` in the next-match section). The market, the base rate and the
home-win rate are references: they appear beside the headline, never as it. Why:
they are the two models that read both teams' strength; the references exist to
be compared with.

### The focus story is a priority list of bands

The story card names one band, taken in this order from the stored simulation
(`storyBand`): relegation when P(relegation) is at least 10 %, title when
P(title) is at least 10 %, Champions League when P(top places) is at least 25 %,
Europe when P(Europe) is at least 25 %, otherwise between. A club in a relegation
fight is not also described as chasing Europe. A gap to the market of one point
and a home-away gap of 0.75 points per match (with at least three matches per
venue) are the sizes worth a clause. The sentences are fixed templates over the
stored numbers and make no causal claim.

### The performance verdict needs a margin and a sample

Two scores are called level when they differ by less than 0.002 RPS, and models
are ranked only on a sample of at least 380 matches (one season). A model does
not "beat" the market or the best baseline inside that margin, and a smaller
sample says so instead of ranking.

### The position heatmap is the one sideways scroller with a hint

Every table and chart fits a phone without a hidden sideways scroll, except the
position heatmap, whose 20 columns cannot shrink below a readable cell. Its
region is focusable, named, and carries a visible hint while it scrolls. The hint
is dropped at 60 rem, where the grid fits (an e2e test pins this from 600 to
1280 px).

## Consequences

A change to any threshold above is a decision, not a tweak: it changes this
record and the tests that name the number. A new page that picks a headline
model or writes a sentence over probabilities follows these rules or amends them
here.
