import type { ReactElement, ReactNode } from "react";
import { ChartFigure } from "./ChartFigure";
import { chartIds } from "./chart-ids";
import { formatNumber } from "./format";
import { bandScale, linearScale, niceMax } from "./scale";
import styles from "./charts.module.css";

export type LineSeries = {
  readonly name: string;
  readonly values: readonly (number | null)[];
  readonly emphasis?: boolean;
};

export type LineChartProps = {
  readonly xLabels: readonly string[];
  readonly series: readonly LineSeries[];
  readonly yLabel?: string;
  readonly valueSuffix?: string;
  readonly id?: string;
  readonly title: string;
  readonly description: string;
  readonly note?: ReactNode;
};

const WIDTH = 480;
const PLOT_LEFT = 38;
const PLOT_RIGHT = 360;
const PLOT_TOP = 18;
const PLOT_BOTTOM = 212;
const HEIGHT = 244;

const SERIES_COLOURS: readonly string[] = [
  // Every fallback keeps at least 3:1 against both the light and the dark page
  // background, so a chart is legible before the theme tokens exist.
  "var(--cat-1, #b45309)",
  "var(--cat-2, #0f766e)",
  "var(--cat-3, #cc79a7)",
  "var(--cat-4, #33608c)",
];

const MUTED_COLOUR = "var(--muted, #6b7280)";
const ACCENT_COLOUR = "var(--accent, #0072b2)";

type EndLabel = {
  readonly name: string;
  readonly value: number;
  readonly x: number;
  y: number;
  readonly colour: string;
};

export function LineChart(props: LineChartProps): ReactElement {
  const {
    xLabels,
    series,
    yLabel,
    valueSuffix,
    id,
    title,
    description,
    note,
  } = props;

  if (series.length > 5) {
    throw new Error(
      `LineChart accepts at most 5 series, but received ${series.length}. Split the chart or keep the five most relevant series.`,
    );
  }

  const ids = chartIds(id ?? title);
  const numeric = series.flatMap((item) =>
    item.values.filter((value): value is number => value !== null && Number.isFinite(value)),
  );
  const rawMax = numeric.length > 0 ? Math.max(...numeric) : 1;
  const rawMin = numeric.length > 0 ? Math.min(...numeric) : 0;
  const yMin = rawMin < 0 ? -niceMax(-rawMin) : 0;
  const yMax = Math.max(yMin + 1, rawMax > 0 ? niceMax(rawMax) : 1);
  const yScale = linearScale([yMin, yMax], [PLOT_BOTTOM, PLOT_TOP]);
  const xScale = bandScale(xLabels.length, [PLOT_LEFT, PLOT_RIGHT], 0.15);
  const yTicks = yScale.ticks(4);

  // The emphasised series takes the accent; the others take the categorical
  // palette in the order they appear, so no counter has to be threaded through
  // the render.
  const categoricalOrder = series
    .map((item, index) => ({ index, emphasised: item.emphasis === true }))
    .filter((entry) => !entry.emphasised)
    .map((entry) => entry.index);
  const colours = series.map((item, index) => {
    if (item.emphasis === true) {
      return ACCENT_COLOUR;
    }
    return SERIES_COLOURS[categoricalOrder.indexOf(index)] ?? MUTED_COLOUR;
  });

  const xTickStep = Math.max(1, Math.ceil(xLabels.length / 6));
  const unitText = [yLabel, valueSuffix !== undefined ? `(${valueSuffix})` : undefined]
    .filter((part): part is string => part !== undefined && part.length > 0)
    .join(" ");

  const endLabels: EndLabel[] = [];
  series.forEach((item, seriesIndex) => {
    for (let index = Math.min(item.values.length, xLabels.length) - 1; index >= 0; index -= 1) {
      const value = item.values[index];
      if (value !== null && value !== undefined && Number.isFinite(value)) {
        endLabels.push({
          name: item.name,
          value,
          x: xScale.center(index),
          y: yScale.scale(value),
          colour: colours[seriesIndex],
        });
        return;
      }
    }
  });

  endLabels.sort((left, right) => left.y - right.y);
  endLabels.forEach((label, index) => {
    const minimum = index === 0 ? PLOT_TOP : endLabels[index - 1].y + 11;
    label.y = Math.max(label.y, minimum);
  });
  const overflow = endLabels.length > 0 ? endLabels[endLabels.length - 1].y - PLOT_BOTTOM : 0;
  if (overflow > 0) {
    for (const label of endLabels) {
      label.y -= overflow;
    }
  }

  return (
    <ChartFigure
      title={title}
      description={description}
      table={{
        caption: title,
        columns: ["x", ...series.map((item) => item.name)],
        rows: xLabels.map((label, index) => [
          label,
          ...series.map((item) => {
            const value = item.values[index];
            if (value === null || value === undefined || !Number.isFinite(value)) {
              return "—";
            }
            return `${formatNumber(value)}${valueSuffix ?? ""}`;
          }),
        ]),
      }}
      note={note}
    >
      <svg
        role="img"
        aria-labelledby={ids.labelledBy}
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        className={styles.svg}
        preserveAspectRatio="xMidYMid meet"
      >
        <title id={ids.titleId}>{title}</title>
        <desc id={ids.descId}>{description}</desc>
        {yTicks.map((tick) => {
          const y = yScale.scale(tick);
          return (
            <g key={`y-tick-${tick}`}>
              <line x1={PLOT_LEFT} y1={y} x2={PLOT_RIGHT} y2={y} className={styles.gridLine} />
              <text x={PLOT_LEFT - 6} y={y} textAnchor="end" dominantBaseline="central" className={styles.axisText}>
                {formatNumber(tick, tick % 1 === 0 ? 0 : 1)}
              </text>
            </g>
          );
        })}
        <line x1={PLOT_LEFT} y1={PLOT_BOTTOM} x2={PLOT_RIGHT} y2={PLOT_BOTTOM} className={styles.axisLine} />
        {unitText.length > 0 ? (
          <text x={PLOT_LEFT} y={10} textAnchor="start" className={styles.axisText}>
            {unitText}
          </text>
        ) : null}
        {xLabels.map((label, index) =>
          index % xTickStep === 0 ? (
            <text
              key={`x-tick-${label}-${index}`}
              x={xScale.center(index)}
              y={PLOT_BOTTOM + 15}
              textAnchor="middle"
              className={styles.axisText}
            >
              {label}
            </text>
          ) : null,
        )}
        {series.map((item, seriesIndex) => {
          const colour = colours[seriesIndex];
          let drawing = false;
          let path = "";
          const limit = Math.min(item.values.length, xLabels.length);
          for (let index = 0; index < limit; index += 1) {
            const value = item.values[index];
            if (value === null || value === undefined || !Number.isFinite(value)) {
              drawing = false;
              continue;
            }
            const x = xScale.center(index);
            const y = yScale.scale(value);
            path += `${drawing ? " L" : " M"} ${x} ${y}`;
            drawing = true;
          }
          return (
            <path
              key={item.name}
              d={path.trim()}
              fill="none"
              strokeWidth={item.emphasis === true ? 2.4 : 1.6}
              strokeLinecap="round"
              strokeLinejoin="round"
              style={{ stroke: colour }}
            />
          );
        })}
        {endLabels.map((label, labelIndex) => {
          const labelX = label.x + 6;
          return (
            <g key={`${label.name}-${labelIndex}`}>
              <line
                x1={label.x}
                y1={label.y}
                x2={labelX - 2}
                y2={label.y}
                style={{ stroke: label.colour, strokeWidth: 1 }}
              />
              <text
                x={labelX}
                y={label.y}
                dominantBaseline="central"
                textAnchor="start"
                className={styles.endLabel}
              >
                {`${label.name} ${formatNumber(label.value)}${valueSuffix ?? ""}`}
              </text>
            </g>
          );
        })}
      </svg>
    </ChartFigure>
  );
}
