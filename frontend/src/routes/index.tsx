import { useState } from "react";
import { Link, createFileRoute } from "@tanstack/react-router";
import { ErrorState } from "@/components/ErrorState";
import { Pagination } from "@/components/Pagination";
import { LinkButton } from "@/components/link-button";
import { Checkbox } from "@/components/ui/checkbox";
import { BookmarkCard } from "@/features/bookmarks/components/BookmarkCard";
import { BookmarkFilters } from "@/features/bookmarks/components/BookmarkFilters";
import { BookmarkListSkeleton } from "@/features/bookmarks/components/BookmarkListSkeleton";
import { SelectionBar } from "@/features/bookmarks/components/SelectionBar";
import {
  bookmarkSearchSchema,
  hasActiveFilters,
  type BookmarkSearch,
} from "@/features/bookmarks/filters";
import { useBookmarks } from "@/features/bookmarks/hooks";
import { plural } from "@/lib/format";
import type { Bookmark } from "@/lib/types";

export const Route = createFileRoute("/")({
  validateSearch: bookmarkSearchSchema,
  component: BookmarksPage,
});

function BookmarksPage() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  const bookmarks = useBookmarks(search);
  // Selection is kept across pages and filters, keyed by id, with the
  // bookmark itself so the actions know which ones have audio.
  const [selected, setSelected] = useState<Map<string, Bookmark>>(new Map());

  function updateSearch(patch: Partial<BookmarkSearch>) {
    void navigate({ search: (prev) => ({ ...prev, ...patch, page: undefined }) });
  }

  function toggle(items: Bookmark[], on: boolean) {
    setSelected((prev) => {
      const next = new Map(prev);
      for (const b of items) {
        if (on) next.set(b.id, b);
        else next.delete(b.id);
      }
      return next;
    });
  }

  const items = bookmarks.data?.items ?? [];
  // Prefer the freshest copy: polling can add audio to a selected bookmark.
  const current = new Map(items.map((b) => [b.id, b]));
  const selection = [...selected.values()].map((b) => current.get(b.id) ?? b);
  const pageSelected = items.length > 0 && items.every((b) => selected.has(b.id));

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-lg font-medium">Bookmarks</h1>
      <BookmarkFilters
        key={search.search ?? ""}
        search={search}
        onChange={updateSearch}
        onReset={() => void navigate({ search: {} })}
      />

      {selected.size > 0 && (
        <SelectionBar selected={selection} onClear={() => setSelected(new Map())} />
      )}

      {bookmarks.isPending ? (
        <BookmarkListSkeleton />
      ) : bookmarks.isError ? (
        <ErrorState
          title="Could not load bookmarks"
          error={bookmarks.error}
          onRetry={() => void bookmarks.refetch()}
        />
      ) : items.length === 0 ? (
        <div className="text-muted-foreground flex flex-col items-center gap-3 rounded-xl border border-dashed p-8 text-center text-sm">
          {hasActiveFilters(search) ? (
            <>
              <p>No bookmarks match these filters.</p>
              <LinkButton variant="outline" size="sm" render={<Link to="/" search={{}} />}>
                Clear filters
              </LinkButton>
            </>
          ) : (
            <>
              <p>No bookmarks yet. Bookmarks saved in Readeck show up here after the next sync.</p>
              <LinkButton variant="outline" size="sm" render={<Link to="/settings" />}>
                Sync settings
              </LinkButton>
            </>
          )}
        </div>
      ) : (
        <>
          <div className="flex items-center gap-2 px-3 text-sm">
            <Checkbox
              id="select-page"
              checked={pageSelected}
              onCheckedChange={(checked) => toggle(items, checked)}
            />
            <label htmlFor="select-page" className="cursor-pointer">
              Select page
            </label>
            <span className="text-muted-foreground ml-auto">
              {plural(bookmarks.data.total, "bookmark")}
            </span>
          </div>
          <ul className="flex flex-col gap-3">
            {items.map((bookmark) => (
              <li key={bookmark.id}>
                <BookmarkCard
                  bookmark={bookmark}
                  selected={selected.has(bookmark.id)}
                  onSelectedChange={(on) => toggle([bookmark], on)}
                />
              </li>
            ))}
          </ul>
          <Pagination
            page={bookmarks.data.page}
            totalPages={bookmarks.data.totalPages}
            onPageChange={(page) =>
              void navigate({ search: (prev) => ({ ...prev, page: page > 1 ? page : undefined }) })
            }
          />
        </>
      )}
    </div>
  );
}
