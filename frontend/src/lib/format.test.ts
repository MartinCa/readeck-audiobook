import { describe, expect, it } from "vitest";
import { describeCron, formatDay, formatUtcDay, jobStatusLabel, plural } from "@/lib/format";

describe("formatDay", () => {
  it("formats an ISO timestamp as a day", () => {
    expect(formatDay("2026-05-01T10:00:00Z")).toBe("1 May 2026");
  });

  it("shows a dash for a missing or broken date", () => {
    expect(formatDay(null)).toBe("—");
    expect(formatDay("not a date")).toBe("—");
  });
});

describe("formatUtcDay", () => {
  it("uses the UTC day whatever the viewer's time zone", () => {
    expect(formatUtcDay("2025-07-01T00:00:00Z")).toBe("1 Jul 2025");
    expect(formatUtcDay("2025-06-30T23:30:00Z")).toBe("30 Jun 2025");
  });

  it("shows a dash for a missing or broken date", () => {
    expect(formatUtcDay(undefined)).toBe("—");
    expect(formatUtcDay("nope")).toBe("—");
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

describe("jobStatusLabel", () => {
  it("shows how far a running job has got", () => {
    expect(jobStatusLabel("processing", { done: 3, total: 8 })).toBe("Generating audio · 37%");
    expect(jobStatusLabel("processing", { done: 0, total: 8 })).toBe("Generating audio · 0%");
  });

  it("says the audio is finishing once every chunk is spoken", () => {
    expect(jobStatusLabel("processing", { done: 8, total: 8 })).toBe("Finishing audio");
  });

  it("falls back to the plain label without progress", () => {
    expect(jobStatusLabel("processing", null)).toBe("Generating audio");
    expect(jobStatusLabel("pending", null)).toBe("Queued");
  });
});
