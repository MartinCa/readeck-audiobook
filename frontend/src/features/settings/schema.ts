import { z } from "zod";

const cronField = z
  .string()
  .trim()
  .refine((v) => v.split(/\s+/).length >= 5, "A cron expression has five fields, e.g. 0 * * * *");

export const autoGenerationSchema = z.object({
  enabled: z.boolean(),
  // An empty field means "no start date": every existing article qualifies.
  since: z.union([z.literal(""), z.string().regex(/^\d{4}-\d{2}-\d{2}$/, "Pick a date")]),
  cron: cronField,
});

export type AutoGenerationForm = z.infer<typeof autoGenerationSchema>;

export const syncSchema = z.object({ cron: cronField });

export type SyncForm = z.infer<typeof syncSchema>;
