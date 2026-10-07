import { describe, expect, it } from "vitest";
import { bookmarkSearchSchema, hasActiveFilters } from "@/features/bookmarks/filters";

describe("bookmarkSearchSchema", () => {
  it("keeps valid filters", () => {
    const search = bookmarkSearchSchema.parse({
      page: 2,
      audio: "without",
      autoGeneration: "excluded",
      addedFrom: "2026-01-01",
    });
    expect(search).toEqual({
      page: 2,
      audio: "without",
      autoGeneration: "excluded",
      addedFrom: "2026-01-01",
    });
  });

  it("accepts the article filter", () => {
    expect(bookmarkSearchSchema.parse({ article: "without" })).toEqual({ article: "without" });
    expect(bookmarkSearchSchema.parse({ article: "nope" })).toEqual({ article: undefined });
  });

  it("drops malformed values instead of failing the page", () => {
    const search = bookmarkSearchSchema.parse({ page: 0, audio: "maybe", addedTo: "yesterday" });
    expect(search).toEqual({ page: undefined, audio: undefined, addedTo: undefined });
  });
});

describe("hasActiveFilters", () => {
  it("ignores the page and 'any' choices", () => {
    expect(hasActiveFilters({ page: 3, audio: "any", autoGeneration: "any" })).toBe(false);
  });

  it("counts the article filter, but not its 'any' choice", () => {
    expect(hasActiveFilters({ article: "without" })).toBe(true);
    expect(hasActiveFilters({ article: "any" })).toBe(false);
  });

  it("sees a single date bound", () => {
    expect(hasActiveFilters({ publishedTo: "2025-12-31" })).toBe(true);
  });
});
