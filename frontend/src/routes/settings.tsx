import { createFileRoute } from "@tanstack/react-router";
import { ErrorState } from "@/components/ErrorState";
import { Skeleton } from "@/components/ui/skeleton";
import { AutoGenerationForm } from "@/features/settings/components/AutoGenerationForm";
import { useSettings } from "@/features/settings/hooks";

export const Route = createFileRoute("/settings")({ component: SettingsPage });

function SettingsPage() {
  const settings = useSettings();

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-lg font-medium">Settings</h1>
      {settings.isPending ? (
        <Skeleton className="h-80 w-full rounded-xl" />
      ) : settings.isError ? (
        <ErrorState
          title="Could not load settings"
          error={settings.error}
          onRetry={() => void settings.refetch()}
        />
      ) : (
        <AutoGenerationForm status={settings.data.autoGeneration} />
      )}
    </div>
  );
}
