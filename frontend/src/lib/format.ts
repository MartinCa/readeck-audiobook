import cronstrue from "cronstrue";
import { format, formatDistanceToNow, isValid, parseISO } from "date-fns";

/** "4 Oct 2026" — for dates where the time of day is noise. */
export function formatDay(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = parseISO(iso);
  return isValid(date) ? format(date, "d MMM yyyy") : "—";
}

/** "4 Oct 2026, 14:05" in the viewer's time zone. */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = parseISO(iso);
  return isValid(date) ? format(date, "d MMM yyyy, HH:mm") : "—";
}

export function formatRelative(iso: string | null | undefined): string {
  if (!iso) return "never";
  const date = parseISO(iso);
  return isValid(date) ? formatDistanceToNow(date, { addSuffix: true }) : "never";
}

/** A plain-English reading of a cron expression, or "" when it does not parse. */
export function describeCron(expr: string): string {
  try {
    return cronstrue.toString(expr, { use24HourTimeFormat: true });
  } catch {
    return "";
  }
}

export function plural(count: number, noun: string): string {
  return `${count} ${noun}${count === 1 ? "" : "s"}`;
}
