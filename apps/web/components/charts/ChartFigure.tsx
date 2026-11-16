import type { ReactElement, ReactNode } from "react";
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
};

export function ChartFigure(props: ChartFigureProps): ReactElement {
  const { title, table, note, children } = props;

  return (
    <figure className={styles.figure}>
      <figcaption className={styles.caption}>{title}</figcaption>
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
