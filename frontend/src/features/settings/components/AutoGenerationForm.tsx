import { zodResolver } from "@hookform/resolvers/zod";
import { PlayIcon } from "lucide-react";
import { Controller, useForm, useWatch } from "react-hook-form";
import { ActionButton } from "@/components/action-button";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { useRunAutoGeneration, useSaveSettings } from "@/features/settings/hooks";
import {
  autoGenerationSchema,
  type AutoGenerationForm as FormValues,
} from "@/features/settings/schema";
import { ApiError } from "@/lib/api";
import { describeCron, formatDateTime, formatRelative, plural } from "@/lib/format";
import { notifications } from "@/lib/notifications";
import type { AutoGenerationStatus } from "@/lib/types";

export function AutoGenerationForm({ status }: { status: AutoGenerationStatus }) {
  const save = useSaveSettings();
  const run = useRunAutoGeneration();
  const form = useForm<FormValues>({
    resolver: zodResolver(autoGenerationSchema),
    values: { enabled: status.enabled, since: status.since ?? "", cron: status.cron },
    // A refetch (say after Run now) must not wipe a change not yet saved.
    resetOptions: { keepDirtyValues: true },
  });
  const cron = useWatch({ control: form.control, name: "cron" });
  const since = useWatch({ control: form.control, name: "since" });
  const cronText = describeCron(cron);
  const errors = form.formState.errors;

  const onSubmit = form.handleSubmit((values) => {
    save.mutate(
      {
        autoGeneration: {
          enabled: values.enabled,
          since: values.since || null,
          cron: values.cron,
        },
      },
      {
        onSuccess: () => notifications.success("Settings saved"),
        onError: (error) => {
          const cronError = error instanceof ApiError && error.fieldErrors["autoGeneration.cron"];
          if (cronError) form.setError("cron", { message: cronError });
          else notifications.error("Could not save settings", { description: error.message });
        },
      },
    );
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Auto audio generation</CardTitle>
        <CardDescription>
          On a schedule, look for articles in Readeck that have never had audio generated and queue
          them. Each article is picked up once: deleting its audio does not bring it back.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={(e) => void onSubmit(e)} className="flex flex-col gap-5" noValidate>
          <div className="flex items-center gap-3">
            <Controller
              control={form.control}
              name="enabled"
              render={({ field }) => (
                <Switch
                  id="auto-enabled"
                  checked={field.value}
                  onCheckedChange={(checked) => field.onChange(checked)}
                />
              )}
            />
            <Label htmlFor="auto-enabled">Enable auto audio generation</Label>
          </div>

          <div className="flex flex-col gap-2">
            <Label htmlFor="auto-since">Only articles added on or after (optional)</Label>
            <div className="flex gap-2">
              <Input
                id="auto-since"
                type="date"
                className="max-w-48"
                aria-invalid={Boolean(errors.since)}
                {...form.register("since")}
              />
              {since && (
                <Button
                  type="button"
                  variant="ghost"
                  onClick={() => form.setValue("since", "", { shouldDirty: true })}
                >
                  Clear
                </Button>
              )}
            </div>
            <p className="text-muted-foreground text-xs">
              Leave empty to generate audio for every existing article too. New articles are always
              included.
            </p>
            {errors.since && <p className="text-destructive text-xs">{errors.since.message}</p>}
          </div>

          <div className="flex flex-col gap-2">
            <Label htmlFor="auto-cron">Schedule (cron)</Label>
            <Input
              id="auto-cron"
              className="max-w-48 font-mono"
              spellCheck={false}
              autoComplete="off"
              aria-invalid={Boolean(errors.cron)}
              aria-describedby="auto-cron-help"
              {...form.register("cron")}
            />
            <p id="auto-cron-help" className="text-muted-foreground text-xs">
              {cronText
                ? `${cronText}, in the server's time zone.`
                : "minute hour day month weekday"}
            </p>
            {errors.cron && <p className="text-destructive text-xs">{errors.cron.message}</p>}
          </div>

          <div className="flex flex-col gap-2 sm:flex-row">
            <Button type="submit" className="w-full sm:w-auto" disabled={save.isPending}>
              Save settings
            </Button>
          </div>
        </form>

        <dl className="mt-6 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 border-t pt-4 text-sm">
          <dt className="text-muted-foreground">Next run</dt>
          <dd>{status.nextRun ? formatDateTime(status.nextRun) : "Not scheduled"}</dd>
          <dt className="text-muted-foreground">Last run</dt>
          <dd>
            {status.lastRun ? formatRelative(status.lastRun) : "Never"}
            {status.lastRun && status.lastError == null && status.lastQueued != null && (
              <>, queued {plural(status.lastQueued, "article")}</>
            )}
          </dd>
          {status.lastError && (
            <>
              <dt className="text-muted-foreground">Last error</dt>
              <dd className="text-status-error break-words">{status.lastError}</dd>
            </>
          )}
        </dl>
        <div className="mt-4">
          <ActionButton
            icon={PlayIcon}
            status={
              run.isPending ? "pending" : run.isError ? "error" : run.isSuccess ? "success" : "idle"
            }
            label="Look for new articles now, whether or not auto generation is on"
            onClick={() =>
              run.mutate(undefined, {
                onSuccess: ({ queued }) =>
                  notifications.success(
                    queued ? `Queued ${plural(queued, "article")}` : "No new articles to queue",
                  ),
                onError: (error) =>
                  notifications.error("Auto generation run failed", {
                    description: error.message,
                  }),
              })
            }
          >
            Run now
          </ActionButton>
        </div>
      </CardContent>
    </Card>
  );
}
