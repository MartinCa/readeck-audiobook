import { useState } from "react";
import { Link, createFileRoute } from "@tanstack/react-router";
import { Trash2Icon, XIcon } from "lucide-react";
import { z } from "zod";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { ErrorState } from "@/components/ErrorState";
import { Pagination } from "@/components/Pagination";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Skeleton } from "@/components/ui/skeleton";
import { jobsApi } from "@/features/jobs/api";
import { JobCard } from "@/features/jobs/components/JobCard";
import { useDeleteJobs, useJobs } from "@/features/jobs/hooks";
import { plural } from "@/lib/format";
import { notifications } from "@/lib/notifications";

const jobsSearchSchema = z.object({
  page: z.number().int().min(1).optional().catch(undefined),
});

export const Route = createFileRoute("/jobs")({
  validateSearch: jobsSearchSchema,
  component: JobsPage,
});

function JobsPage() {
  const { page = 1 } = Route.useSearch();
  const navigate = Route.useNavigate();
  const jobs = useJobs(page);
  const removeMany = useDeleteJobs();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [confirmDelete, setConfirmDelete] = useState(false);

  const items = jobs.data?.items ?? [];
  const total = jobs.data?.total ?? 0;
  const pageIds = items.map((j) => j.id);
  const pageSelected = pageIds.length > 0 && pageIds.every((id) => selected.has(id));

  function toggle(ids: string[], on: boolean) {
    setSelected((prev) => {
      const next = new Set(prev);
      for (const id of ids) {
        if (on) next.add(id);
        else next.delete(id);
      }
      return next;
    });
  }

  async function selectAll() {
    try {
      setSelected(new Set(await jobsApi.allIds()));
    } catch (error) {
      notifications.error("Could not select every job", {
        description: error instanceof Error ? error.message : String(error),
      });
    }
  }

  function deleteSelected() {
    removeMany.mutate([...selected], {
      onSuccess: ({ count }) => {
        notifications.success(`Deleted ${plural(count, "job")}`);
        setSelected(new Set());
        setConfirmDelete(false);
      },
      onError: (error) =>
        notifications.error("Could not delete the jobs", { description: error.message }),
    });
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-baseline gap-2">
        <h1 className="text-lg font-medium">Jobs</h1>
        {jobs.data && <span className="text-muted-foreground text-sm">{plural(total, "job")}</span>}
      </div>
      <p className="text-muted-foreground text-sm">
        Audio that is queued, being generated, or failed. Finished audio is on its bookmark.
      </p>

      {jobs.isPending ? (
        <div className="flex flex-col gap-3" aria-busy="true" aria-label="Loading jobs">
          {Array.from({ length: 3 }, (_, i) => (
            <Skeleton key={i} className="h-16 w-full rounded-xl" />
          ))}
        </div>
      ) : jobs.isError ? (
        <ErrorState
          title="Could not load jobs"
          error={jobs.error}
          onRetry={() => void jobs.refetch()}
        />
      ) : items.length === 0 ? (
        <div className="text-muted-foreground flex flex-col items-center gap-3 rounded-xl border border-dashed p-8 text-center text-sm">
          <p>Nothing is queued, generating or failed.</p>
          <Button variant="outline" size="sm" render={<Link to="/" />} nativeButton={false}>
            Pick bookmarks to generate audio for
          </Button>
        </div>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2 px-3 text-sm">
            <Checkbox
              id="select-page"
              checked={pageSelected}
              onCheckedChange={(checked) => toggle(pageIds, checked)}
            />
            <label htmlFor="select-page" className="cursor-pointer">
              Select page
            </label>
            {pageSelected && selected.size < total && (
              <Button variant="ghost" size="sm" onClick={() => void selectAll()}>
                Select all {total}
              </Button>
            )}
            {selected.size > 0 && (
              <>
                <span className="text-muted-foreground">{selected.size} selected</span>
                <Button variant="ghost" size="sm" onClick={() => setSelected(new Set())}>
                  <XIcon aria-hidden />
                  Clear
                </Button>
                <div className="flex-1" />
                <Button variant="destructive" size="sm" onClick={() => setConfirmDelete(true)}>
                  <Trash2Icon aria-hidden />
                  Delete selected
                </Button>
              </>
            )}
          </div>
          <ul className="flex flex-col gap-3">
            {items.map((job) => (
              <li key={job.id}>
                <JobCard
                  job={job}
                  selected={selected.has(job.id)}
                  onSelectedChange={(on) => toggle([job.id], on)}
                />
              </li>
            ))}
          </ul>
          <Pagination
            page={page}
            totalPages={jobs.data.totalPages}
            onPageChange={(p) => void navigate({ search: { page: p > 1 ? p : undefined } })}
          />
        </>
      )}

      <ConfirmDialog
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        title={`Delete ${plural(selected.size, "job")}?`}
        description="Queued jobs are taken off the queue and failed ones are removed from the list."
        confirmLabel="Delete jobs"
        pending={removeMany.isPending}
        onConfirm={deleteSelected}
      />
    </div>
  );
}
