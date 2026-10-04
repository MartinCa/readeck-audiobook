import { CircleAlertIcon } from "lucide-react";
import { Button } from "@/components/ui/button";

interface ErrorStateProps {
  title: string;
  error: Error;
  onRetry: () => void;
}

/** What failed, the server's explanation, and a way to try again. */
export function ErrorState({ title, error, onRetry }: ErrorStateProps) {
  return (
    <div
      role="alert"
      className="border-destructive/30 flex flex-col items-start gap-3 rounded-xl border p-4"
    >
      <div className="flex items-center gap-2 font-medium">
        <CircleAlertIcon className="text-status-error size-4" aria-hidden />
        {title}
      </div>
      <p className="text-muted-foreground text-sm break-words">{error.message}</p>
      <Button variant="outline" size="sm" onClick={onRetry}>
        Try again
      </Button>
    </div>
  );
}
