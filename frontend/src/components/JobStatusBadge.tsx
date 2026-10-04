import { Loader2Icon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { jobStatusLabel } from "@/lib/format";
import type { Job } from "@/lib/types";

export function JobStatusBadge({
  status,
  progress,
}: {
  status: Job["status"];
  progress?: Job["progress"];
}) {
  return (
    <Badge variant={status === "failed" ? "destructive" : "secondary"} className="tabular-nums">
      {status === "processing" && (
        <Loader2Icon className="animate-spin motion-reduce:animate-none" aria-hidden />
      )}
      {jobStatusLabel(status, progress)}
    </Badge>
  );
}
