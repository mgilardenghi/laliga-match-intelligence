/**
 * The application shell.
 *
 * Pages import the shell from here and never from the individual files, so a
 * component can move inside the directory without touching a page. The two
 * selectors are client components; everything else renders on the server.
 */
export { Header } from "./Header";
export { Nav } from "./Nav";
export { Footer } from "./Footer";
export { SkipLink } from "./SkipLink";
export { ThemeToggle } from "./ThemeToggle";
export { SeasonSelect } from "./SeasonSelect";
export type { SeasonOption, SeasonSelectProps } from "./SeasonSelect";
export { TeamSelect } from "./TeamSelect";
export type { TeamOption, TeamSelectProps } from "./TeamSelect";
