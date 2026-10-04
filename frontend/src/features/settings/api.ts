import { api } from "@/lib/api";
import type { QueueResult, Settings, SettingsUpdate } from "@/lib/types";

export const settingsApi = {
  get: () => api.get<Settings>("/settings"),
  put: (body: SettingsUpdate) => api.put<Settings>("/settings", body),
  runAutoGeneration: () => api.post<QueueResult>("/auto-generation/run"),
};
