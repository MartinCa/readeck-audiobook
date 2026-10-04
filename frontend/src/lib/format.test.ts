import { describe, expect, it } from "vitest";
import { describeCron, formatDay, plural } from "@/lib/format";

describe("formatDay", () => {
  it("formats an ISO timestamp as a day", () => {
    expect(formatDay("2026-05-01T10:00:00Z")).toBe("1 May 2026");
  });

  it("shows a dash for a missing or broken date", () => {
    expect(formatDay(null)).toBe("—");
    expect(formatDay("not a date")).toBe("—");
  });
});

describe("describeCron", () => {
  it("reads a valid expression", () => {
    expect(describeCron("0 * * * *")).toBe("Every hour");
  });

  it("returns an empty string for nonsense", () => {
    expect(describeCron("every hour")).toBe("");
  });
});

describe("plural", () => {
  it("pluralises everything but one", () => {
    expect(plural(1, "job")).toBe("1 job");
    expect(plural(0, "job")).toBe("0 jobs");
  });
});
