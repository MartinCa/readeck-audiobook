import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { jobsApi } from "@/features/jobs/api";
import type { JobPage } from "@/lib/types";

const POLL_MS = 4000;

export const jobsKey = ["jobs"] as const;

function hasRunningJob(page: JobPage | undefined): boolean {
  return Boolean(page?.items.some((j) => j.status === "pending" || j.status === "processing"));
}

export function useJobs(page: number) {
  return useQuery({
    queryKey: [...jobsKey, page],
    queryFn: () => jobsApi.list(page),
    placeholderData: keepPreviousData,
    // One request per interval for the whole page, and only while a job on
    // it is still moving. A job that completes drops off this list.
    refetchInterval: (query) => (hasRunningJob(query.state.data) ? POLL_MS : false),
  });
}

/** Job changes show on the Bookmarks page too (status badges, audio). */
function useInvalidateAll() {
  const queryClient = useQueryClient();
  return () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: jobsKey }),
      queryClient.invalidateQueries({ queryKey: ["bookmarks"] }),
    ]);
}

export function useRetryJob() {
  const invalidate = useInvalidateAll();
  return useMutation({ mutationFn: jobsApi.retry, onSuccess: invalidate });
}

export function useDeleteJob() {
  const invalidate = useInvalidateAll();
  return useMutation({ mutationFn: jobsApi.remove, onSuccess: invalidate });
}

export function useDeleteJobs() {
  const invalidate = useInvalidateAll();
  return useMutation({ mutationFn: jobsApi.removeMany, onSuccess: invalidate });
}
