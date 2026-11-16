/**
 * The shared UI foundation.
 *
 * The components are Server Components: no `"use client"`, no hook, no fetch
 * and no JavaScript. Pages import them from this barrel and never from the
 * individual files, so a component can move inside the directory without
 * touching a page.
 */
export { MetricHelp } from "./MetricHelp";
export type { MetricHelpProps } from "./MetricHelp";
export { HowToRead } from "./HowToRead";
export type { HowToReadItem, HowToReadProps } from "./HowToRead";
export { SectionNav } from "./SectionNav";
export type { SectionNavItem, SectionNavProps } from "./SectionNav";
export { StatTile } from "./StatTile";
export type { StatTileHelp, StatTileProps, StatTone } from "./StatTile";
export { Legend } from "./Legend";
export type { LegendItem, LegendProps, SemanticToken } from "./Legend";
