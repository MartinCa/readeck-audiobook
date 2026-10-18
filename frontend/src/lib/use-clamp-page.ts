import { useEffect } from "react";

/**
 * Moves a paged list back to its last page when the requested one no longer
 * exists, e.g. a reload on `?page=2` after the list shrank to a single page.
 * Without it the list shows as empty with no pagination to get back with.
 */
export function useClampPage(
  page: number,
  totalPages: number | undefined,
  onClamp: (lastPage: number) => void,
) {
  useEffect(() => {
    if (totalPages !== undefined && page > totalPages) onClamp(Math.max(1, totalPages));
  }, [page, totalPages, onClamp]);
}
