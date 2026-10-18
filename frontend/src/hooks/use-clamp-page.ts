import { useEffect, useEffectEvent } from "react";

interface PageData {
  page: number;
  totalPages: number;
  items: readonly unknown[];
}

/** The last page to show instead of `page`, or null when `page` exists. */
export function clampedPage(page: number, totalPages: number): number | null {
  return page > totalPages ? Math.max(1, totalPages) : null;
}

/**
 * What to do about the page the URL asks for, given the latest response.
 *
 * Only a response for that page says whether it exists: while a new query
 * loads, `keepPreviousData` still hands back the old one, whose `totalPages`
 * means nothing here. `loading` stays true while that stand-in is an empty page,
 * which would otherwise flash as an empty list.
 */
export function clampTarget(
  page: number,
  data: PageData | undefined,
): { last: number | null; loading: boolean } {
  if (!data) return { last: null, loading: false };
  if (data.page !== page) return { last: null, loading: data.items.length === 0 };
  const last = clampedPage(page, data.totalPages);
  return { last, loading: last !== null };
}

/**
 * Moves a paged list back to its last page when the requested one no longer
 * exists, e.g. a reload on `?page=2` after the list shrank to a single page.
 * Without it the list shows as empty with no pagination to get back with.
 * Returns true while that is under way, so the caller can show its loading
 * state instead of an empty list.
 */
export function useClampPage(
  page: number,
  data: PageData | undefined,
  onClamp: (lastPage: number) => void,
): boolean {
  const { last, loading } = clampTarget(page, data);
  const clamp = useEffectEvent(onClamp);
  useEffect(() => {
    if (last !== null) clamp(last);
  }, [last]);
  return loading;
}
