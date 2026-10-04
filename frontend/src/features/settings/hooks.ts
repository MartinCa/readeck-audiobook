import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { settingsApi } from "@/features/settings/api";
import type { Settings } from "@/lib/types";

const settingsKey = ["settings"] as const;

export function useSettings() {
  const queryClient = useQueryClient();
  return useQuery({
    queryKey: settingsKey,
    queryFn: async () => {
      const wasSyncing = queryClient.getQueryData<Settings>(settingsKey)?.sync.running;
      const settings = await settingsApi.get();
      // A sync that just finished may have added or removed bookmarks.
      if (wasSyncing && !settings.sync.running) {
        void queryClient.invalidateQueries({ queryKey: ["bookmarks"] });
      }
      return settings;
    },
    // Follow a running sync until it finishes.
    refetchInterval: (query) => (query.state.data?.sync.running ? 2000 : false),
  });
}

export function useSaveSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: settingsApi.put,
    onSuccess: (data) => queryClient.setQueryData(settingsKey, data),
  });
}

export function useRunAutoGeneration() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: settingsApi.runAutoGeneration,
    onSettled: () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: settingsKey }),
        queryClient.invalidateQueries({ queryKey: ["jobs"] }),
        queryClient.invalidateQueries({ queryKey: ["bookmarks"] }),
      ]),
  });
}

export function useRunSync() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: settingsApi.runSync,
    onSuccess: (data) => queryClient.setQueryData(settingsKey, data),
  });
}
