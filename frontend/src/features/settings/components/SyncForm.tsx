import { zodResolver } from "@hookform/resolvers/zod";
import { RefreshCwIcon } from "lucide-react";
import { useForm, useWatch } from "react-hook-form";
import { ActionButton } from "@/components/action-button";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useRunSync, useSaveSettings } from "@/features/settings/hooks";
import { syncSchema, type SyncForm as FormValues } from "@/features/settings/schema";
import { ApiError } from "@/lib/api";
import { describeCron, formatDateTime, formatRelative, plural } from "@/lib/format";
import { notifications } from "@/lib/notifications";
import type { SyncStatus } from "@/lib/types";

function describeChanges(status: SyncStatus): string {
  const parts = [
    status.added && `${status.added} added`,
    status.updated && `${status.updated} updated`,
    status.removed && `${status.removed} removed`,
  ].filter(Boolean);
  return parts.length ? parts.join(", ") : "no changes";
}

export function SyncForm({ status }: { status: SyncStatus }) {
  const save = useSaveSettings();
  const run = useRunSync();
  const form = useForm<FormValues>({
    resolver: zodResolver(syncSchema),
    values: { cron: status.cron },
    // The settings refetch while a sync runs; keep a change not yet saved.
    resetOptions: { keepDirtyValues: true },
  });
  const cron = useWatch({ control: form.control, name: "cron" });
  const cronText = describeCron(cron);
  const errors = form.formState.errors;

  const onSubmit = form.handleSubmit((values) => {
    save.mutate(
      { sync: { cron: values.cron } },
      {
        onSuccess: () => notifications.success("Sync schedule saved"),
        onError: (error) => {
          const cronError = error instanceof ApiError && error.fieldErrors["sync.cron"];
          if (cronError) form.setError("cron", { message: cronError });
          else
            notifications.error("Could not save the sync schedule", { description: error.message });
        },
      },
    );
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Readeck sync</CardTitle>
        <CardDescription>
          Bookmarks are copied from Readeck so the list stays fast with thousands of them. Each sync
          picks up new and changed bookmarks, and removes the ones deleted in Readeck along with
          their audio.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={(e) => void onSubmit(e)} className="flex flex-col gap-5" noValidate>
          <div className="flex flex-col gap-2">
            <Label htmlFor="sync-cron">Schedule (cron)</Label>
            <Input
              id="sync-cron"
              className="max-w-48 font-mono"
              spellCheck={false}
              autoComplete="off"
              aria-invalid={Boolean(errors.cron)}
              aria-describedby="sync-cron-help"
              {...form.register("cron")}
            />
            <p id="sync-cron-help" className="text-muted-foreground text-xs">
              {cronText
                ? `${cronText}, in the server's time zone. The app also syncs when it starts.`
                : "minute hour day month weekday"}
            </p>
            {errors.cron && <p className="text-destructive text-xs">{errors.cron.message}</p>}
          </div>

          <div className="flex flex-col gap-2 sm:flex-row">
            <Button type="submit" className="w-full sm:w-auto" disabled={save.isPending}>
              Save schedule
            </Button>
          </div>
        </form>

        <dl className="mt-6 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 border-t pt-4 text-sm">
          <dt className="text-muted-foreground">Bookmarks</dt>
          <dd>{plural(status.bookmarkCount, "bookmark")}</dd>
          <dt className="text-muted-foreground">Last sync</dt>
          <dd>
            {status.running
              ? "Syncing now…"
              : status.lastRun
                ? formatRelative(status.lastRun)
                : "Never"}
            {!status.running && status.lastRun && status.lastError == null && (
              <>, {describeChanges(status)}</>
            )}
          </dd>
          <dt className="text-muted-foreground">Next sync</dt>
          <dd>{status.nextRun ? formatDateTime(status.nextRun) : "Not scheduled"}</dd>
          {status.lastError && !status.running && (
            <>
              <dt className="text-muted-foreground">Last error</dt>
              <dd className="text-status-error break-words">{status.lastError}</dd>
            </>
          )}
        </dl>
        <div className="mt-4">
          <ActionButton
            icon={RefreshCwIcon}
            status={status.running || run.isPending ? "pending" : run.isError ? "error" : "idle"}
            disabled={status.running}
            label="Sync with Readeck now"
            onClick={() =>
              run.mutate(undefined, {
                onError: (error) =>
                  notifications.error("Could not start a sync", { description: error.message }),
              })
            }
          >
            Sync now
          </ActionButton>
        </div>
      </CardContent>
    </Card>
  );
}
