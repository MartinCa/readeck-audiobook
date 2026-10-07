import { plural } from "@/lib/format";
import type { QueueResult } from "@/lib/types";

export interface QueueMessage {
  kind: "success" | "info" | "warning";
  title: string;
  description?: string;
}

/** What to tell the user after asking for audio: nothing is announced as queued unless it was. */
export function describeQueueResult({ queued, skipped, noArticle }: QueueResult): QueueMessage {
  const notes = [
    skipped > 0 && `${skipped} already queued or generating`,
    noArticle > 0 && `${noArticle} without article text in Readeck`,
  ].filter(Boolean);
  const description = notes.length > 0 ? `Skipped: ${notes.join(", ")}.` : undefined;
  if (queued > 0) {
    return {
      kind: "success",
      title: `Queued ${plural(queued, "bookmark")} for audio`,
      ...(description && { description }),
    };
  }
  return {
    // Nothing to read out is a problem to act on; already queued is just a note.
    kind: noArticle > 0 ? "warning" : "info",
    title: "Nothing was queued",
    ...(description && { description }),
  };
}
