import { describe, expect, it } from "vitest";
import { autoGenerationSchema, generationSchema, syncSchema } from "@/features/settings/schema";

describe("autoGenerationSchema", () => {
  it("accepts an empty start date", () => {
    const result = autoGenerationSchema.safeParse({ enabled: true, since: "", cron: "0 * * * *" });
    expect(result.success).toBe(true);
  });

  it("rejects a cron expression with too few fields", () => {
    const result = autoGenerationSchema.safeParse({ enabled: true, since: "", cron: "0 *" });
    expect(result.success).toBe(false);
  });
});

describe("syncSchema", () => {
  it("trims the cron expression", () => {
    expect(syncSchema.parse({ cron: " */15 * * * * " })).toEqual({ cron: "*/15 * * * *" });
  });
});

describe("generationSchema", () => {
  it("accepts a whole number up to 10000", () => {
    expect(generationSchema.safeParse({ minArticleWords: "30" }).success).toBe(true);
    expect(generationSchema.safeParse({ minArticleWords: "0" }).success).toBe(true);
    expect(generationSchema.safeParse({ minArticleWords: "10001" }).success).toBe(false);
    expect(generationSchema.safeParse({ minArticleWords: "-1" }).success).toBe(false);
    expect(generationSchema.safeParse({ minArticleWords: "" }).success).toBe(false);
  });
});
