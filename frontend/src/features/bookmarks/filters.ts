import { z } from "zod";
import type { AudioFilter, ExclusionFilter } from "@/lib/types";

const isoDate = z.string().regex(/^\d{4}-\d{2}-\d{2}$/);

/**
 * The Bookmarks page's filters, kept in the URL so a filtered view survives a
 * reload and can be bookmarked. Anything malformed is dropped, not an error.
 */
export const bookmarkSearchSchema = z.object({
  page: z.number().int().min(1).optional().catch(undefined),
  search: z.string().optional().catch(undefined),
  audio: z.enum(["any", "with", "without"]).optional().catch(undefined),
  autoGeneration: z.enum(["any", "excluded", "included"]).optional().catch(undefined),
  addedFrom: isoDate.optional().catch(undefined),
  addedTo: isoDate.optional().catch(undefined),
  publishedFrom: isoDate.optional().catch(undefined),
  publishedTo: isoDate.optional().catch(undefined),
});

export type BookmarkSearch = z.infer<typeof bookmarkSearchSchema>;

export const audioOptions: { value: AudioFilter; label: string }[] = [
  { value: "any", label: "Any audio" },
  { value: "with", label: "Has audio" },
  { value: "without", label: "No audio" },
];

export const exclusionOptions: { value: ExclusionFilter; label: string }[] = [
  { value: "any", label: "Any auto generation" },
  { value: "included", label: "Auto generation on" },
  { value: "excluded", label: "Excluded from auto" },
];

/** True when anything narrows the list, so the empty state can say so. */
export function hasActiveFilters(search: BookmarkSearch): boolean {
  return Boolean(
    search.search ||
    (search.audio && search.audio !== "any") ||
    (search.autoGeneration && search.autoGeneration !== "any") ||
    search.addedFrom ||
    search.addedTo ||
    search.publishedFrom ||
    search.publishedTo,
  );
}
