import { Loader2Icon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { useVoxtralStatus } from "@/features/settings/hooks";
import { formatDuration, formatRelative } from "@/lib/format";
import type { VoxtralStatus } from "@/lib/types";

const stateLabels: Record<string, string> = {
  unmanaged: "Always on",
  stopped: "Stopped",
  starting: "Starting",
  running: "Running",
  stopping: "Stopping",
  error: "Unknown",
};

function describe(status: VoxtralStatus): string {
  switch (status.state) {
    case "unmanaged":
      return "The app does not control this server and assumes it is always running. Set DOCKER_PROXY_URL and VOXTRAL_CONTAINER to have it stopped when idle and started on demand.";
    case "stopped":
      return "The container is stopped and holds no GPU memory. The next Voxtral job starts it and waits until it answers, which takes a few minutes.";
    case "starting": {
      const since = status.startTriggeredAt ?? status.startedAt;
      return `Starting${since ? `, triggered ${formatRelative(since)}` : ""}. Jobs wait until it answers.`;
    }
    case "running":
      if (status.inUse) return "In use by a job. It is stopped after the last one has been idle.";
      if (status.stopInSeconds != null) {
        return `Stops in ${formatDuration(status.stopInSeconds)} unless a job needs it first.`;
      }
      return status.idleStopSeconds === 0
        ? "Stays running: stopping when idle is turned off."
        : "Running.";
    case "stopping":
      return "Stopping. The next Voxtral job starts it again.";
    default:
      return status.detail ?? "The container's state could not be read.";
  }
}

export function VoxtralStatusPanel() {
  const { data, isPending, isError } = useVoxtralStatus(true);
  const busy = data?.state === "starting" || data?.state === "stopping";

  return (
    <div className="bg-muted/40 flex flex-col gap-2 rounded-lg border p-3 text-sm sm:max-w-xl">
      <div className="flex items-center gap-2">
        <span className="font-medium">Voxtral server</span>
        {isPending ? (
          <Badge variant="secondary">Checking</Badge>
        ) : isError ? (
          <Badge variant="destructive">Unavailable</Badge>
        ) : (
          <Badge variant={data.state === "error" ? "destructive" : "secondary"}>
            {busy && (
              <Loader2Icon className="animate-spin motion-reduce:animate-none" aria-hidden />
            )}
            {stateLabels[data.state] ?? data.state}
          </Badge>
        )}
      </div>
      <p className="text-muted-foreground text-xs" aria-live="polite">
        {isPending
          ? "Reading the container's state."
          : isError
            ? "The app could not be asked for the server's state."
            : describe(data)}
      </p>
    </div>
  );
}
