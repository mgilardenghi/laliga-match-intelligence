/** Every user-facing date and time is shown in this zone. */
export const MADRID_TIME_ZONE = "Europe/Madrid";

const DATE_FORMAT = new Intl.DateTimeFormat("en-GB", {
  timeZone: MADRID_TIME_ZONE,
  day: "2-digit",
  month: "short",
  year: "numeric",
});

const DATE_TIME_FORMAT = new Intl.DateTimeFormat("en-GB", {
  timeZone: MADRID_TIME_ZONE,
  day: "2-digit",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/;
const WALL_CLOCK_TIME = /^([01]\d|2[0-3]):[0-5]\d(?::[0-5]\d)?$/;
// An ISO date-time carries `Z` or a numeric offset. Without one, `new Date`
// would interpret it in the server's own zone, which is exactly the bug this
// module exists to prevent.
const ISO_WITH_ZONE = /T.*(?:Z|[+-]\d{2}:?\d{2})$/;
const EM_DASH = "—";

function toDate(value: string): Date | null {
  if (DATE_ONLY.test(value)) {
    // A calendar date has no instant. Parsing it as UTC midnight keeps it on
    // the same Madrid calendar day for either offset.
    const date = new Date(`${value}T00:00:00Z`);
    return Number.isNaN(date.getTime()) ? null : date;
  }
  if (!ISO_WITH_ZONE.test(value)) {
    return null;
  }
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

/**
 * Return an ISO date-time rendered in Europe/Madrid, or the em dash.
 *
 * The API's `kickoff_time` is a wall-clock time with no date and no timezone:
 * it is already Madrid local time, so it is returned as `HH:MM` and must be
 * shown next to its kickoff date rather than combined into a date-time and
 * reparsed as an instant. A date-only value renders as a date, and `null`, an
 * invalid date or a date-time with no timezone renders as the em dash.
 */
export function formatMadridDateTime(
  value: string | Date | null | undefined,
): string {
  if (value === null || value === undefined) {
    return EM_DASH;
  }
  if (typeof value === "string") {
    const trimmed = value.trim();
    if (WALL_CLOCK_TIME.test(trimmed)) {
      return trimmed.slice(0, 5);
    }
    if (DATE_ONLY.test(trimmed)) {
      const date = toDate(trimmed);
      return date === null ? EM_DASH : DATE_FORMAT.format(date);
    }
    const date = toDate(trimmed);
    return date === null ? EM_DASH : DATE_TIME_FORMAT.format(date);
  }
  return Number.isNaN(value.getTime())
    ? EM_DASH
    : DATE_TIME_FORMAT.format(value);
}

/**
 * Return the Europe/Madrid calendar date of a value, or the em dash.
 *
 * A full timestamp is converted to Madrid before its date is formatted, so a
 * late UTC instant lands on the next Madrid day. `null`, an invalid value and a
 * bare wall-clock time have no date and render as the em dash.
 */
export function formatMadridDate(
  value: string | Date | null | undefined,
): string {
  if (value === null || value === undefined) {
    return EM_DASH;
  }
  if (typeof value === "string") {
    const trimmed = value.trim();
    if (WALL_CLOCK_TIME.test(trimmed)) {
      return EM_DASH;
    }
    const date = toDate(trimmed);
    return date === null ? EM_DASH : DATE_FORMAT.format(date);
  }
  return Number.isNaN(value.getTime()) ? EM_DASH : DATE_FORMAT.format(value);
}
