import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { bookmarksApi } from "@/features/bookmarks/api";
import type { BookmarkSearch } from "@/features/bookmarks/filters";
import type { BookmarkPage } from "@/lib/types";

const POLL_MS = 4000;

export const bookmarksKey = ["bookmarks"] as const;

function hasRunningJob(page: BookmarkPage | undefined): boolean {
  return Boolean(
    page?.items.some((b) => b.job?.status === "pending" || b.job?.status === "processing"),
  );
}

export function useBookmarks(search: BookmarkSearch) {
  return useQuery({
    queryKey: [...bookmarksKey, search],
    queryFn: () => bookmarksApi.list(search),
    placeholderData: keepPreviousData,
    // Only while something on this page is queued or generating, so finished
    // audio appears without a reload; an idle page makes no requests.
    refetchInterval: (query) => (hasRunningJob(query.state.data) ? POLL_MS : false),
  });
}

/** Every bookmark action changes what the Bookmarks and Jobs pages show. */
function useInvalidateAll() {
  const queryClient = useQueryClient();
  return () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: bookmarksKey }),
      queryClient.invalidateQueries({ queryKey: ["jobs"] }),
    ]);
}

export function useGenerateAudio() {
  const invalidate = useInvalidateAll();
  return useMutation({ mutationFn: bookmarksApi.generate, onSuccess: invalidate });
}

export function useDeleteAudio() {
  const invalidate = useInvalidateAll();
  return useMutation({ mutationFn: bookmarksApi.deleteAudio, onSuccess: invalidate });
}

export function useArchiveInReadeck() {
  const invalidate = useInvalidateAll();
  return useMutation({ mutationFn: bookmarksApi.archiveInReadeck, onSuccess: invalidate });
}

export function useDeleteInReadeck() {
  const invalidate = useInvalidateAll();
  return useMutation({ mutationFn: bookmarksApi.deleteInReadeck, onSuccess: invalidate });
}

export function useSetAutoExcluded() {
  const invalidate = useInvalidateAll();
  return useMutation({
    mutationFn: ({ ids, excluded }: { ids: string[]; excluded: boolean }) =>
      bookmarksApi.setAutoExcluded(ids, excluded),
    onSuccess: invalidate,
  });
}
