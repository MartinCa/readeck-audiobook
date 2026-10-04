import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { settingsApi } from "@/features/settings/api";

const settingsKey = ["settings"] as const;

export function useSettings() {
  return useQuery({ queryKey: settingsKey, queryFn: settingsApi.get });
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
