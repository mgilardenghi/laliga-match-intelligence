/**
 * Human labels for the API's vocabularies.
 *
 * Every table is checked against the contract's own literal union with
 * `satisfies`, so a new contract value fails the type check until it gets a
 * label. A value that still arrives unknown is returned unchanged rather than
 * rendering as `undefined`.
 */
export type Outcome = "H" | "D" | "A";
export type ModelName =
  | "home_win"
  | "base_rate"
  | "market"
  | "elo"
  | "dixon_coles";
export type MatchStatus = "scheduled" | "played";
export type IngestionStatus = "running" | "succeeded" | "failed";
export type ResultLetter = "W" | "D" | "L";

const OUTCOME_LABELS = {
  H: "Home win",
  D: "Draw",
  A: "Away win",
} satisfies Record<Outcome, string>;

const MODEL_LABELS = {
  home_win: "Home-win baseline",
  base_rate: "Base rate",
  market: "Market",
  elo: "Elo",
  dixon_coles: "Dixon-Coles",
} satisfies Record<ModelName, string>;

const MATCH_STATUS_LABELS = {
  scheduled: "Scheduled",
  played: "Played",
} satisfies Record<MatchStatus, string>;

const INGESTION_STATUS_LABELS = {
  running: "Running",
  succeeded: "Succeeded",
  failed: "Failed",
} satisfies Record<IngestionStatus, string>;

const RESULT_LETTERS = {
  H: "W",
  D: "D",
  A: "L",
} satisfies Record<Outcome, ResultLetter>;

function labelFrom(table: Record<string, string>, value: string): string {
  return Object.prototype.hasOwnProperty.call(table, value) ? table[value] : value;
}

/**
 * Return the label of a stored outcome, or the raw value when it is unknown.
 */
export function outcomeLabel(value: string): string {
  return labelFrom(OUTCOME_LABELS, value);
}

/**
 * Return the display name of a model, or the raw name when it is unknown.
 */
export function modelLabel(value: string): string {
  return labelFrom(MODEL_LABELS, value);
}

/**
 * Return the label of a fixture status, or the raw status when it is unknown.
 */
export function matchStatusLabel(value: string): string {
  return labelFrom(MATCH_STATUS_LABELS, value);
}

/**
 * Return the label of an ingestion status, or the raw status when it is unknown.
 */
export function ingestionStatusLabel(value: string): string {
  return labelFrom(INGESTION_STATUS_LABELS, value);
}

/**
 * Return the form letter of a stored result (`H` to `W`, `A` to `L`).
 *
 * An unknown value is returned unchanged, so a contract addition still renders.
 */
export function outcomeLetter(value: string): string {
  return labelFrom(RESULT_LETTERS, value);
}
