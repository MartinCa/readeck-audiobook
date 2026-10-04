import { api } from "@/lib/api";
import type { CountResult, Job, JobPage } from "@/lib/types";

export const jobsApi = {
  list: (page: number) => api.get<JobPage>("/jobs", { query: { page } }),
  allIds: () => api.get<string[]>("/jobs/ids"),
  retry: (id: string) => api.post<Job>(`/jobs/${encodeURIComponent(id)}/retry`),
  remove: (id: string) => api.delete<void>(`/jobs/${encodeURIComponent(id)}`),
  removeMany: (jobIds: string[]) => api.post<CountResult>("/jobs/bulk-delete", { jobIds }),
};
