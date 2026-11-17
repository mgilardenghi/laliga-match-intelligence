/**
 * The columns of the standings table, in the order the table draws them.
 *
 * The table has two widths. The default fits a laptop; the widened view adds
 * the result breakdown and the Europe chance behind `?columns=all`. Both are
 * decided here, so the header row and the body cells cannot disagree, and the
 * projected columns appear only when a run exists.
 */
import { formatCount } from "@/lib/format";
import { firstValue } from "@/lib/params";

/** Which of the two widths the table draws. */
export type ColumnsView = "default" | "all";

/** Every column the table can draw, in reading order. */
export type ColumnKey =
  | "pos"
  | "club"
  | "p"
  | "w"
  | "d"
  | "l"
  | "gf"
  | "ga"
  | "gd"
  | "pts"
  | "form"
  | "xpts"
  | "title"
  | "ucl"
  | "europe"
  | "rel";

/** One header cell: which column it is and the label it shows. */
export type Column = {
  readonly key: ColumnKey;
  readonly label: string;
};

/** The widest view's query value. */
const ALL = "all";

/**
 * Return the view a `columns` query parameter asks for.
 *
 * Only the exact value `all` widens the table; an absent parameter, an unknown
 * word or a repeated parameter whose first value is not `all` keeps the default
 * width. A repeated parameter is read through `firstValue`, like every other
 * parameter of the page. The page accepts no other view, so anything else is
 * the same as saying nothing.
 */
export function parseColumns(value: string | string[] | undefined): ColumnsView {
  return firstValue(value) === ALL ? "all" : "default";
}

/**
 * Return the header cells of the table in DOM order.
 *
 * Without a run the table is the current standings only, so the projected
 * columns are absent and the view adds nothing but the result breakdown.
 */
export function columnsFor(options: {
  readonly hasRun: boolean;
  readonly view: ColumnsView;
  readonly ucl: number;
  readonly europe: number;
}): Column[] {
  const { hasRun, view, ucl, europe } = options;
  const all = view === "all";
  const columns: Column[] = [
    { key: "pos", label: "#" },
    { key: "club", label: "Club" },
    { key: "p", label: "P" },
  ];
  if (all) {
    columns.push(
      { key: "w", label: "W" },
      { key: "d", label: "D" },
      { key: "l", label: "L" },
      { key: "gf", label: "GF" },
      { key: "ga", label: "GA" },
    );
  }
  columns.push(
    { key: "gd", label: "GD" },
    { key: "pts", label: "Pts" },
    { key: "form", label: "Last 5" },
  );
  if (hasRun) {
    columns.push(
      { key: "xpts", label: "Final pts" },
      { key: "title", label: "Title" },
      { key: "ucl", label: `Top ${formatCount(ucl)}` },
    );
    if (all) {
      columns.push({ key: "europe", label: `Top ${formatCount(europe)}` });
    }
    columns.push({ key: "rel", label: "Relegated" });
  }
  return columns;
}
