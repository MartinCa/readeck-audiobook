import cronstrue from "cronstrue";
import { format, formatDistanceToNow, isValid, parseISO } from "date-fns";
import type { Job } from "@/lib/types";

/** "4 Oct 2026" — for dates where the time of day is noise. */
export function formatDay(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = parseISO(iso);
  return isValid(date) ? format(date, "d MMM yyyy") : "—";
}

/**
 * The UTC calendar day of a timestamp, e.g. "4 Oct 2026". For publication
 * dates: sites mostly give a bare date, which Readeck stores as midnight UTC,
 * and the server filters on the UTC day too.
 */
export function formatUtcDay(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = parseISO(iso);
  if (!isValid(date)) return "—";
  return format(
    new Date(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate()),
    "d MMM yyyy",
  );
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

/** "12:34", or "1:05:09" from an hour up; "—" when the length is unknown. */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return "—";
  const total = Math.round(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = String(total % 60).padStart(2, "0");
  return hours > 0 ? `${hours}:${String(minutes).padStart(2, "0")}:${secs}` : `${minutes}:${secs}`;
}

export function plural(count: number, noun: string): string {
  return `${count} ${noun}${count === 1 ? "" : "s"}`;
}

const jobStatusLabels: Record<Job["status"], string> = {
  pending: "Queued",
  processing: "Generating audio",
  completed: "Audio ready",
  failed: "Failed",
};

/** The badge text, with how far through the article a running job is. */
export function jobStatusLabel(status: Job["status"], progress?: Job["progress"]) {
  if (status !== "processing" || !progress?.total) return jobStatusLabels[status];
  // Every chunk is spoken, but the file is still being encoded and tagged.
  if (progress.done >= progress.total) return "Finishing audio";
  return `${jobStatusLabels.processing} · ${Math.floor((progress.done / progress.total) * 100)}%`;
}
