import { describe, expect, it } from "vitest";
import { clampTarget, clampedPage } from "./use-clamp-page";

describe("clampedPage", () => {
  it("leaves a page that exists alone", () => {
    expect(clampedPage(1, 1)).toBeNull();
    expect(clampedPage(2, 3)).toBeNull();
    expect(clampedPage(3, 3)).toBeNull();
  });

  it("moves a page past the end to the last one", () => {
    expect(clampedPage(2, 1)).toBe(1);
    expect(clampedPage(9, 3)).toBe(3);
  });

  it("never goes below the first page", () => {
    expect(clampedPage(2, 0)).toBe(1);
  });
});

describe("clampTarget", () => {
  const data = (page: number, totalPages: number, items: unknown[] = []) => ({
    page,
    totalPages,
    items,
  });

  it("does nothing before there is a response", () => {
    expect(clampTarget(2, undefined)).toEqual({ last: null, loading: false });
  });

  it("clamps from a response for the requested page", () => {
    expect(clampTarget(2, data(2, 1))).toEqual({ last: 1, loading: true });
  });

  it("leaves a page that exists alone", () => {
    expect(clampTarget(2, data(2, 3, ["a"]))).toEqual({ last: null, loading: false });
  });

  it("does not clamp against a stand-in for another page", () => {
    // Back to page 3 while the response still on hand is page 2 of 1.
    expect(clampTarget(3, data(2, 1, ["a"]))).toEqual({ last: null, loading: false });
  });

  it("keeps loading while the stand-in is an empty page", () => {
    // Just clamped from page 2 to 1: the empty page 2 is still on hand.
    expect(clampTarget(1, data(2, 1))).toEqual({ last: null, loading: true });
  });
});
