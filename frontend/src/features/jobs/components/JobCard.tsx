import { useState } from "react";
import { ExternalLinkIcon, RotateCcwIcon, Trash2Icon } from "lucide-react";
import { ActionButton } from "@/components/action-button";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { JobStatusBadge } from "@/components/JobStatusBadge";
import { Card } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { useDeleteJob, useRetryJob } from "@/features/jobs/hooks";
import { formatDateTime, formatRelative } from "@/lib/format";
import { notifications } from "@/lib/notifications";
import type { ActionStatus } from "@/hooks/use-async-action";
import type { Job } from "@/lib/types";

interface JobCardProps {
  job: Job;
  selected: boolean;
  onSelectedChange: (selected: boolean) => void;
}

function mutationStatus(m: { isPending: boolean; isSuccess: boolean; isError: boolean }) {
  const status: ActionStatus = m.isPending
    ? "pending"
    : m.isError
      ? "error"
      : m.isSuccess
        ? "success"
        : "idle";
  return status;
}

export function JobCard({ job, selected, onSelectedChange }: JobCardProps) {
  const [confirmDelete, setConfirmDelete] = useState(false);
  const retry = useRetryJob();
  const remove = useDeleteJob();
  const titleId = `job-${job.id}-title`;
  const engine = job.voice ? `${job.ttsEngine} (${job.voice})` : job.ttsEngine;

  return (
    <Card size="sm" className={selected ? "ring-primary/60 ring-2" : undefined}>
      <div className="flex gap-3 px-3">
        <Checkbox
          checked={selected}
          onCheckedChange={(checked) => onSelectedChange(checked)}
          aria-labelledby={titleId}
          className="mt-1"
        />
        <div className="flex min-w-0 flex-1 flex-col gap-1.5">
          <div className="flex items-start gap-2">
            <h2 id={titleId} className="min-w-0 flex-1 font-medium break-words">
              {job.bookmarkTitle}
            </h2>
            <JobStatusBadge status={job.status} progress={job.progress} />
          </div>
          <p className="text-muted-foreground text-xs break-words">
            {engine} · queued{" "}
            <time dateTime={job.createdAt} title={formatDateTime(job.createdAt)}>
              {formatRelative(job.createdAt)}
            </time>
          </p>
          {job.status === "failed" && job.errorMsg && (
            <p className="text-status-error text-xs break-words">{job.errorMsg}</p>
          )}
        </div>
        <div className="flex shrink-0 items-start gap-1">
          {job.bookmarkUrl && (
            <a
              href={job.bookmarkUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="text-muted-foreground hover:text-foreground focus-visible:ring-ring/50 inline-flex size-7 items-center justify-center rounded-md outline-none focus-visible:ring-3"
              aria-label={`Open the original of ${job.bookmarkTitle}`}
            >
              <ExternalLinkIcon className="size-4" aria-hidden />
            </a>
          )}
          {job.status === "failed" && (
            <ActionButton
              icon={RotateCcwIcon}
              label="Retry"
              resultLabel={retry.isError ? "Retry failed" : "Queued again"}
              status={mutationStatus(retry)}
              variant="ghost"
              size="icon-sm"
              onClick={() =>
                retry.mutate(job.id, {
                  onSuccess: () => notifications.success("Queued again"),
                  onError: (error) =>
                    notifications.error("Could not retry the job", { description: error.message }),
                })
              }
            />
          )}
          <ActionButton
            icon={Trash2Icon}
            label="Delete job"
            status={mutationStatus(remove)}
            variant="ghost"
            size="icon-sm"
            onClick={() => setConfirmDelete(true)}
          />
        </div>
      </div>
      <ConfirmDialog
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        title="Delete this job?"
        description={
          job.status === "failed"
            ? "The failed job is removed from the list."
            : "The job is taken off the queue. You can queue the bookmark again later."
        }
        confirmLabel="Delete job"
        pending={remove.isPending}
        onConfirm={() =>
          remove.mutate(job.id, {
            onSuccess: () => setConfirmDelete(false),
            onError: (error) =>
              notifications.error("Could not delete the job", { description: error.message }),
          })
        }
      />
    </Card>
  );
}
