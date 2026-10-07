import { describe, expect, it } from "vitest";
import { describeQueueResult } from "@/features/bookmarks/queueMessage";

describe("describeQueueResult", () => {
  it("announces what was queued, with a note on what was skipped", () => {
    expect(describeQueueResult({ queued: 2, skipped: 1, noArticle: 1 })).toEqual({
      kind: "success",
      title: "Queued 2 bookmarks for audio",
      description: "Skipped: 1 already queued or generating, 1 without article text in Readeck.",
    });
  });

  it("has no description when nothing was skipped", () => {
    expect(describeQueueResult({ queued: 1, skipped: 0, noArticle: 0 })).toEqual({
      kind: "success",
      title: "Queued 1 bookmark for audio",
    });
  });

  it("never claims success when nothing was queued", () => {
    expect(describeQueueResult({ queued: 0, skipped: 3, noArticle: 0 })).toEqual({
      kind: "info",
      title: "Nothing was queued",
      description: "Skipped: 3 already queued or generating.",
    });
  });

  it("warns when the reason is missing article text", () => {
    const message = describeQueueResult({ queued: 0, skipped: 1, noArticle: 2 });
    expect(message.kind).toBe("warning");
    expect(message.title).toBe("Nothing was queued");
  });
});
