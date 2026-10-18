import { describe, expect, it } from "vitest";
import { clampedPage } from "./use-clamp-page";

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
