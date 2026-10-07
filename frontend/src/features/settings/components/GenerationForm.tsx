import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useSaveSettings } from "@/features/settings/hooks";
import { generationSchema, type GenerationForm as FormValues } from "@/features/settings/schema";
import { notifications } from "@/lib/notifications";
import type { GenerationSettings } from "@/lib/types";

export function GenerationForm({ settings }: { settings: GenerationSettings }) {
  const save = useSaveSettings();
  const form = useForm<FormValues>({
    resolver: zodResolver(generationSchema),
    values: { minArticleWords: String(settings.minArticleWords) },
    resetOptions: { keepDirtyValues: true },
  });
  const errors = form.formState.errors;

  const onSubmit = form.handleSubmit((values) => {
    save.mutate(
      { generation: { minArticleWords: Number(values.minArticleWords) } },
      {
        onSuccess: () => notifications.success("Settings saved"),
        onError: (error) =>
          notifications.error("Could not save settings", { description: error.message }),
      },
    );
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Audio generation</CardTitle>
        <CardDescription>
          When Readeck fails to extract an article it can leave only a title behind. An article with
          fewer words than the minimum fails with an error instead of becoming a useless audio file.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={(e) => void onSubmit(e)} className="flex flex-col gap-5" noValidate>
          <div className="flex flex-col gap-2">
            <Label htmlFor="min-article-words">Minimum article length (words)</Label>
            <Input
              id="min-article-words"
              className="max-w-32"
              inputMode="numeric"
              autoComplete="off"
              aria-invalid={Boolean(errors.minArticleWords)}
              aria-describedby="min-article-words-help"
              {...form.register("minArticleWords")}
            />
            <p id="min-article-words-help" className="text-muted-foreground text-xs">
              0 turns the check off. The title and Readeck&apos;s metadata header do not count.
            </p>
            {errors.minArticleWords && (
              <p className="text-destructive text-xs">{errors.minArticleWords.message}</p>
            )}
          </div>
          <div className="flex flex-col gap-2 sm:flex-row">
            <Button type="submit" className="w-full sm:w-auto" disabled={save.isPending}>
              Save
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}
