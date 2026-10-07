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

export const generationSchema = z.object({
  // Kept as text while editing; the server takes a whole number up to 10000.
  minArticleWords: z
    .string()
    .trim()
    .regex(/^\d+$/, "Enter a whole number")
    .refine((v) => Number(v) <= 10000, "At most 10000"),
});

export type GenerationForm = z.infer<typeof generationSchema>;
