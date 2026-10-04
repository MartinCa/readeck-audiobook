import { describe, expect, it } from "vitest";
import { autoGenerationSchema } from "@/features/settings/schema";

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
