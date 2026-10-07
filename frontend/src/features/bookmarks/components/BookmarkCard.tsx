import { useState } from "react";
import {
  ArchiveIcon,
  AudioLinesIcon,
  BanIcon,
  BookmarkIcon,
  DownloadIcon,
  EllipsisVerticalIcon,
  ExternalLinkIcon,
  FileXIcon,
  Trash2Icon,
} from "lucide-react";
import { ActionButton } from "@/components/action-button";
import { JobStatusBadge } from "@/components/JobStatusBadge";
import { Badge } from "@/components/ui/badge";
import { LinkButton } from "@/components/link-button";
import { Card } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  ReadeckActionDialog,
  type ReadeckAction,
} from "@/features/bookmarks/components/ReadeckActionDialog";
import { describeQueueResult } from "@/features/bookmarks/queueMessage";
import { useGenerateAudio } from "@/features/bookmarks/hooks";
import { notifications } from "@/lib/notifications";
import { formatDateTime, formatDay, formatDuration, formatUtcDay } from "@/lib/format";
import type { Bookmark } from "@/lib/types";

interface BookmarkCardProps {
  bookmark: Bookmark;
  selected: boolean;
  onSelectedChange: (selected: boolean) => void;
  /** Called after the bookmark was archived or deleted, so the page can drop it from its selection. */
  onRemoved: (ids: string[]) => void;
}

export function BookmarkCard({
  bookmark,
  selected,
  onSelectedChange,
  onRemoved,
}: BookmarkCardProps) {
  const [action, setAction] = useState<ReadeckAction | null>(null);
  const generate = useGenerateAudio();
  const meta = [
    bookmark.siteName,
    bookmark.authors.join(", "),
    bookmark.readingTime ? `${bookmark.readingTime} min read` : "",
    bookmark.lang,
    bookmark.audio?.durationSeconds != null
      ? `Audio ${formatDuration(bookmark.audio.durationSeconds)}`
      : "",
  ].filter(Boolean);
  const titleId = `bookmark-${bookmark.id}-title`;
  const generating = bookmark.job?.status === "pending" || bookmark.job?.status === "processing";

  function onGenerate() {
    generate.mutate([bookmark.id], {
      onSuccess: (result) => {
        const { kind, title, description } = describeQueueResult(result);
        notifications[kind](title, { ...(description && { description }) });
      },
      onError: (error) =>
        notifications.error("Could not queue audio generation", { description: error.message }),
    });
  }

  return (
    <Card size="sm" className={selected ? "ring-primary/60 ring-2" : undefined}>
      <div className="flex gap-3 px-3">
        <Checkbox
          checked={selected}
          onCheckedChange={(checked) => onSelectedChange(checked)}
          aria-labelledby={titleId}
          className="mt-1"
        />
        <div className="flex min-w-0 flex-1 flex-col gap-1.5">
          <div className="flex items-start gap-2">
            <h2 id={titleId} className="min-w-0 flex-1 font-medium break-words">
              {bookmark.title}
            </h2>
            <div className="-mt-1 -mr-1.5 flex shrink-0 items-center">
              {bookmark.url && (
                <a
                  href={bookmark.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-muted-foreground hover:text-foreground focus-visible:ring-ring/50 inline-flex size-10 items-center justify-center rounded-md outline-none focus-visible:ring-3 sm:size-8"
                  aria-label={`Open the original of ${bookmark.title}`}
                >
                  <ExternalLinkIcon className="size-4" aria-hidden />
                </a>
              )}
              <DropdownMenu>
                <DropdownMenuTrigger
                  render={
                    <Button
                      variant="ghost"
                      size="icon"
                      className="text-muted-foreground size-10 sm:size-8"
                      aria-label={`Readeck actions for ${bookmark.title}`}
                    />
                  }
                >
                  <EllipsisVerticalIcon aria-hidden />
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end" className="min-w-52">
                  {bookmark.readeckUrl && (
                    <DropdownMenuItem
                      className="min-h-11 sm:min-h-0"
                      render={
                        <a href={bookmark.readeckUrl} target="_blank" rel="noopener noreferrer" />
                      }
                    >
                      <BookmarkIcon aria-hidden />
                      Open in Readeck
                    </DropdownMenuItem>
                  )}
                  <DropdownMenuItem
                    className="min-h-11 sm:min-h-0"
                    onClick={() => setAction("archive")}
                  >
                    <ArchiveIcon aria-hidden />
                    Mark read &amp; archive
                  </DropdownMenuItem>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem
                    variant="destructive"
                    className="min-h-11 sm:min-h-0"
                    onClick={() => setAction("delete")}
                  >
                    <Trash2Icon aria-hidden />
                    Delete from Readeck
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
          </div>

          {meta.length > 0 && (
            <p className="text-muted-foreground text-xs break-words">{meta.join(" · ")}</p>
          )}
          <p className="text-muted-foreground text-xs">
            Published {bookmark.published ? formatUtcDay(bookmark.published) : "date unknown"} ·
            Added {formatDay(bookmark.added)}
          </p>
          {bookmark.description && (
            <p className="text-muted-foreground line-clamp-2 text-sm">{bookmark.description}</p>
          )}

          <div className="flex flex-wrap items-center gap-1.5 empty:hidden">
            {bookmark.type && bookmark.type !== "article" && (
              <Badge variant="outline">{bookmark.type}</Badge>
            )}
            {bookmark.job && (
              <JobStatusBadge status={bookmark.job.status} progress={bookmark.job.progress} />
            )}
            {!bookmark.hasArticle && (
              <Badge variant="outline">
                <FileXIcon aria-hidden />
                No article text
              </Badge>
            )}
            {bookmark.autoExcluded && (
              <Badge variant="outline">
                <BanIcon aria-hidden />
                Excluded from auto generation
              </Badge>
            )}
          </div>
          {bookmark.job?.status === "failed" && bookmark.job.errorMsg && (
            <p className="text-status-error text-xs break-words">{bookmark.job.errorMsg}</p>
          )}

          {!bookmark.hasArticle && !bookmark.audio && (
            <p className="text-muted-foreground text-xs">
              Readeck extracted no article text, so there is nothing to read out. Re-extract the
              bookmark in Readeck to enable audio.
            </p>
          )}

          {!bookmark.audio && !generating && (
            <div className="mt-1">
              <ActionButton
                icon={AudioLinesIcon}
                variant="outline"
                className="h-9 w-full sm:h-7 sm:w-auto"
                status={generate.isPending ? "pending" : "idle"}
                disabled={!bookmark.hasArticle}
                title={
                  bookmark.hasArticle
                    ? undefined
                    : "Readeck extracted no article text for this bookmark"
                }
                onClick={onGenerate}
              >
                Generate audio
              </ActionButton>
            </div>
          )}

          {bookmark.audio && (
            <div className="mt-1 flex flex-col gap-2 sm:flex-row sm:items-center">
              <audio
                controls
                preload="none"
                src={bookmark.audio.url}
                className="h-9 w-full min-w-0 sm:flex-1"
                aria-label={`Audio for ${bookmark.title}`}
              />
              <LinkButton
                variant="outline"
                size="sm"
                render={<a href={bookmark.audio.url} download={bookmark.audio.filename} />}
                title={`Generated ${formatDateTime(bookmark.audio.generatedAt)} with ${bookmark.audio.ttsEngine}`}
              >
                <DownloadIcon aria-hidden />
                Download
              </LinkButton>
            </div>
          )}
        </div>
      </div>
      <ReadeckActionDialog
        action={action}
        ids={[bookmark.id]}
        onClose={() => setAction(null)}
        onDone={onRemoved}
      />
    </Card>
  );
}
