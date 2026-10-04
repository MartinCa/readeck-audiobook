import { Loader2Icon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import type { Job } from "@/lib/types";

const labels: Record<Job["status"], string> = {
  pending: "Queued",
  processing: "Generating audio",
  completed: "Audio ready",
  failed: "Failed",
};

export function JobStatusBadge({ status }: { status: Job["status"] }) {
  return (
    <Badge variant={status === "failed" ? "destructive" : "secondary"}>
      {status === "processing" && (
        <Loader2Icon className="animate-spin motion-reduce:animate-none" aria-hidden />
      )}
      {labels[status]}
    </Badge>
  );
}
