import type { ReactElement, ReactNode } from "react";
import type { ChartIds } from "./chart-ids";
import styles from "./charts.module.css";

export type ChartTable = {
  readonly caption: string;
  readonly columns: readonly string[];
  readonly rows: readonly (readonly (string | number)[])[];
};

export type ChartFigureProps = {
  readonly title: string;
  readonly description: string;
  readonly table: ChartTable;
  readonly note?: ReactNode;
  readonly children: ReactNode;
  /**
   * The aria ids a chart labels its marks with. When they are given, the
   * figcaption carries the title id and a visually hidden span right below it
   * carries the description, so an overlay chart can point `aria-labelledby`
   * at text outside its own scaled drawing. Without them the figure renders
   * neither element, which is what the charts that still draw their own
   * `<title>` and `<desc>` rely on.
   */
  readonly ids?: ChartIds;
};

export function ChartFigure(props: ChartFigureProps): ReactElement {
  const { title, description, table, note, children, ids } = props;

  return (
    <figure className={styles.figure}>
      <figcaption id={ids?.titleId} className={styles.caption}>
        {title}
      </figcaption>
      {ids ? (
        <span id={ids.descId} className={styles.srOnly}>
          {description}
        </span>
      ) : null}
      {children}
      {note ? <p className={styles.note}>{note}</p> : null}
      <details className={styles.details}>
        <summary className={styles.summary}>Data table</summary>
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <caption>{table.caption}</caption>
            <thead>
              <tr>
                {table.columns.map((column) => (
                  <th key={column} scope="col">
                    {column}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {table.rows.map((row, rowIndex) => (
                <tr key={`${rowIndex}-${String(row[0] ?? "")}`}>
                  {row.map((cell, cellIndex) =>
                    cellIndex === 0 ? (
                      <th key={cellIndex} scope="row">
                        {cell}
                      </th>
                    ) : (
                      <td key={cellIndex}>{cell}</td>
                    ),
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </figure>
  );
}
