import { useEffect, useEffectEvent } from "react";

/** The last page to show instead of `page`, or null when `page` exists. */
export function clampedPage(page: number, totalPages: number): number | null {
  return page > totalPages ? Math.max(1, totalPages) : null;
}

/**
 * Moves a paged list back to its last page when the requested one no longer
 * exists, e.g. a reload on `?page=2` after the list shrank to a single page.
 * Without it the list shows as empty with no pagination to get back with.
 *
 * Only a response for the requested page counts: while a new query loads,
 * `keepPreviousData` still hands back the old one, whose `totalPages` says
 * nothing about this page. Returns true while a clamp is due, so the caller
 * can show its loading state instead of a flash of empty list.
 */
export function useClampPage(
  page: number,
  data: { page: number; totalPages: number } | undefined,
  onClamp: (lastPage: number) => void,
): boolean {
  const last = data && data.page === page ? clampedPage(page, data.totalPages) : null;
  const clamp = useEffectEvent(onClamp);
  useEffect(() => {
    if (last !== null) clamp(last);
  }, [last]);
  return last !== null;
}
