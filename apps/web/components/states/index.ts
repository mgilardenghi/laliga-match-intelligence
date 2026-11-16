/**
 * The shared empty, error and loading states.
 *
 * Pages import these from the barrel and never from the individual files, so a
 * state can move inside the directory without touching a page. All three render
 * on the server: none of them fetches data, holds state or reads a browser API,
 * which keeps them usable inside a Server Component and inside a `<Suspense>`
 * fallback alike.
 */
export { EmptyState } from "./EmptyState";
export type { EmptyStateProps } from "./EmptyState";
export { ProblemState } from "./ProblemState";
export type { ProblemStateProps } from "./ProblemState";
export { SectionSkeleton } from "./SectionSkeleton";
export type { SectionSkeletonProps } from "./SectionSkeleton";
