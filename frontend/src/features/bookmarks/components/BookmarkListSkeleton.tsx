import { Skeleton } from "@/components/ui/skeleton";

export function BookmarkListSkeleton() {
  return (
    <div className="flex flex-col gap-3" aria-busy="true" aria-label="Loading bookmarks">
      {Array.from({ length: 5 }, (_, i) => (
        <div key={i} className="ring-foreground/10 flex flex-col gap-2 rounded-xl p-3 ring-1">
          <Skeleton className="h-4 w-3/4" />
          <Skeleton className="h-3 w-1/2" />
          <Skeleton className="h-3 w-1/3" />
        </div>
      ))}
    </div>
  );
}
