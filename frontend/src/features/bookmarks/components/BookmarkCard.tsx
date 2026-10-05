import { DownloadIcon, ExternalLinkIcon, BanIcon } from "lucide-react";
import { JobStatusBadge } from "@/components/JobStatusBadge";
import { Badge } from "@/components/ui/badge";
import { LinkButton } from "@/components/link-button";
import { Card } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { formatDateTime, formatDay, formatUtcDay } from "@/lib/format";
import type { Bookmark } from "@/lib/types";

interface BookmarkCardProps {
  bookmark: Bookmark;
  selected: boolean;
  onSelectedChange: (selected: boolean) => void;
}

export function BookmarkCard({ bookmark, selected, onSelectedChange }: BookmarkCardProps) {
  const meta = [
    bookmark.siteName,
    bookmark.authors.join(", "),
    bookmark.readingTime ? `${bookmark.readingTime} min read` : "",
    bookmark.lang,
  ].filter(Boolean);
  const titleId = `bookmark-${bookmark.id}-title`;

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
            {bookmark.url && (
              <a
                href={bookmark.url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-muted-foreground hover:text-foreground focus-visible:ring-ring/50 shrink-0 rounded-sm outline-none focus-visible:ring-3"
                aria-label={`Open the original of ${bookmark.title}`}
              >
                <ExternalLinkIcon className="size-4" aria-hidden />
              </a>
            )}
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
    </Card>
  );
}
