import { useState } from "react";
import { AudioLinesIcon, BanIcon, CircleCheckIcon, Trash2Icon, XIcon } from "lucide-react";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Button } from "@/components/ui/button";
import { useDeleteAudio, useGenerateAudio, useSetAutoExcluded } from "@/features/bookmarks/hooks";
import { ApiError } from "@/lib/api";
import { plural } from "@/lib/format";
import { notifications } from "@/lib/notifications";
import type { Bookmark } from "@/lib/types";

interface SelectionBarProps {
  selected: Bookmark[];
  onClear: () => void;
}

function describeError(error: unknown): string {
  return error instanceof ApiError || error instanceof Error ? error.message : String(error);
}

/** Actions on the selected bookmarks; each shows only when it applies. */
export function SelectionBar({ selected, onClear }: SelectionBarProps) {
  const [confirmDelete, setConfirmDelete] = useState(false);
  const generate = useGenerateAudio();
  const deleteAudio = useDeleteAudio();
  const setExcluded = useSetAutoExcluded();

  const ids = selected.map((b) => b.id);
  const withAudio = selected.filter((b) => b.audio).map((b) => b.id);
  const included = selected.filter((b) => !b.autoExcluded).map((b) => b.id);
  const excluded = selected.filter((b) => b.autoExcluded).map((b) => b.id);
  const busy = generate.isPending || deleteAudio.isPending || setExcluded.isPending;

  function onGenerate() {
    generate.mutate(ids, {
      onSuccess: ({ queued, skipped }) => {
        notifications.success(`Queued ${plural(queued, "bookmark")} for audio`, {
          ...(skipped > 0 && { description: `${skipped} already queued or generating.` }),
        });
        onClear();
      },
      onError: (error) =>
        notifications.error("Could not queue audio generation", {
          description: describeError(error),
        }),
    });
  }

  function onDeleteAudio() {
    deleteAudio.mutate(withAudio, {
      onSuccess: ({ count }) => {
        notifications.success(`Deleted audio for ${plural(count, "bookmark")}`);
        setConfirmDelete(false);
        onClear();
      },
      onError: (error) =>
        notifications.error("Could not delete the audio", { description: describeError(error) }),
    });
  }

  function onSetExcluded(targetIds: string[], exclude: boolean) {
    setExcluded.mutate(
      { ids: targetIds, excluded: exclude },
      {
        onSuccess: ({ count }) => {
          notifications.success(
            exclude
              ? `Excluded ${plural(count, "bookmark")} from auto generation`
              : `Included ${plural(count, "bookmark")} in auto generation`,
          );
          onClear();
        },
        onError: (error) =>
          notifications.error("Could not change auto generation", {
            description: describeError(error),
          }),
      },
    );
  }

  return (
    <div className="bg-card ring-foreground/10 sticky top-2 z-10 flex flex-wrap items-center gap-2 rounded-xl p-2 shadow-sm ring-1">
      <span className="px-1 text-sm font-medium">{selected.length} selected</span>
      <Button variant="ghost" size="sm" onClick={onClear} disabled={busy}>
        <XIcon aria-hidden />
        Clear
      </Button>
      <div className="flex-1" />
      <Button size="sm" onClick={onGenerate} disabled={busy}>
        <AudioLinesIcon aria-hidden />
        Generate audio
      </Button>
      {withAudio.length > 0 && (
        <Button
          size="sm"
          variant="destructive"
          onClick={() => setConfirmDelete(true)}
          disabled={busy}
        >
          <Trash2Icon aria-hidden />
          Delete audio ({withAudio.length})
        </Button>
      )}
      {included.length > 0 && (
        <Button
          size="sm"
          variant="outline"
          onClick={() => onSetExcluded(included, true)}
          disabled={busy}
        >
          <BanIcon aria-hidden />
          Exclude from auto generation
        </Button>
      )}
      {excluded.length > 0 && (
        <Button
          size="sm"
          variant="outline"
          onClick={() => onSetExcluded(excluded, false)}
          disabled={busy}
        >
          <CircleCheckIcon aria-hidden />
          Include in auto generation
        </Button>
      )}

      <ConfirmDialog
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        title="Delete audio?"
        description={`The audio files of ${plural(withAudio.length, "bookmark")} will be deleted. The bookmarks stay in Readeck, and you can generate the audio again.`}
        confirmLabel="Delete audio"
        onConfirm={onDeleteAudio}
        pending={deleteAudio.isPending}
      />
    </div>
  );
}
