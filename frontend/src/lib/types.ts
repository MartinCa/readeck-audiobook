import type { components } from "@/lib/api-types";

type Schemas = components["schemas"];

export type Bookmark = Schemas["Bookmark"];
export type BookmarkPage = Schemas["BookmarkPage"];
export type Audio = Schemas["Audio"];
export type AudioFilter = Schemas["AudioFilter"];
export type ExclusionFilter = Schemas["ExclusionFilter"];
export type Job = Schemas["Job"];
export type JobPage = Schemas["JobPage"];
export type QueueResult = Schemas["QueueResult"];
export type CountResult = Schemas["CountResult"];
export type Settings = Schemas["Settings"];
export type SettingsUpdate = Schemas["SettingsUpdate"];
export type AutoGenerationStatus = Schemas["AutoGenerationStatus"];
export type SyncStatus = Schemas["SyncStatus"];
