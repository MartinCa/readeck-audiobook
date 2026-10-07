import { api } from "@/lib/api";
import type { BookmarkPage, CountResult, QueueResult, ReadeckActionResult } from "@/lib/types";
import type { BookmarkSearch } from "@/features/bookmarks/filters";

export const bookmarksApi = {
  list: (search: BookmarkSearch) => api.get<BookmarkPage>("/bookmarks", { query: search }),
  generate: (bookmarkIds: string[]) => api.post<QueueResult>("/jobs", { bookmarkIds }),
  deleteAudio: (bookmarkIds: string[]) =>
    api.post<CountResult>("/bookmarks/audio/delete", { bookmarkIds }),
  archiveInReadeck: (bookmarkIds: string[]) =>
    api.post<ReadeckActionResult>("/bookmarks/readeck/archive", { bookmarkIds }),
  deleteInReadeck: (bookmarkIds: string[]) =>
    api.post<ReadeckActionResult>("/bookmarks/readeck/delete", { bookmarkIds }),
  setAutoExcluded: (bookmarkIds: string[], excluded: boolean) =>
    api.put<CountResult>("/bookmarks/auto-generation", { bookmarkIds, excluded }),
};
