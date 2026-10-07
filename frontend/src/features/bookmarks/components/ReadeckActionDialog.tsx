import { ConfirmDialog } from "@/components/ConfirmDialog";
import { useArchiveInReadeck, useDeleteInReadeck } from "@/features/bookmarks/hooks";
import { plural } from "@/lib/format";
import { notifications } from "@/lib/notifications";

export type ReadeckAction = "archive" | "delete";

interface ReadeckActionDialogProps {
  /** The action being confirmed; null keeps the dialog closed. */
  action: ReadeckAction | null;
  ids: string[];
  onClose: () => void;
  /** Called with the ids once the action ran, so a selection can drop them. */
  onDone: (ids: string[]) => void;
}

const COPY = {
  archive: {
    title: "Mark read and archive?",
    confirm: "Archive",
    describe: (n: string) =>
      `${n} will be marked as read and archived in Readeck, then removed here together with the audio. Un-archive it in Readeck to bring it back.`,
  },
  delete: {
    title: "Delete from Readeck?",
    confirm: "Delete",
    describe: (n: string) =>
      `${n} will be deleted from Readeck, then removed here together with the audio. This cannot be undone.`,
  },
} as const;

/** Confirms, runs and reports a Readeck action on bookmarks; used by the card menu and the selection bar. */
export function ReadeckActionDialog({ action, ids, onClose, onDone }: ReadeckActionDialogProps) {
  const archive = useArchiveInReadeck();
  const remove = useDeleteInReadeck();
  const mutation = action === "delete" ? remove : archive;
  const copy = COPY[action ?? "archive"];

  function onConfirm() {
    if (!action) return;
    const verb = action === "delete" ? "Deleted" : "Archived";
    mutation.mutate(ids, {
      onSuccess: ({ count, failed }) => {
        if (count > 0) notifications.success(`${verb} ${plural(count, "bookmark")} in Readeck`);
        if (failed > 0) {
          notifications.error(`${plural(failed, "bookmark")} could not be changed in Readeck`, {
            description: "They are unchanged here. Check that Readeck is reachable and try again.",
          });
        }
        if (count > 0) onDone(ids);
        onClose();
      },
      onError: (error) =>
        notifications.error("Could not reach Readeck", { description: error.message }),
    });
  }

  return (
    <ConfirmDialog
      open={action !== null}
      onOpenChange={(open) => !open && onClose()}
      title={copy.title}
      description={copy.describe(plural(ids.length, "bookmark"))}
      confirmLabel={copy.confirm}
      onConfirm={onConfirm}
      pending={mutation.isPending}
    />
  );
}
