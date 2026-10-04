import { useState, type FormEvent } from "react";
import { SearchIcon, XIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  audioOptions,
  exclusionOptions,
  hasActiveFilters,
  type BookmarkSearch,
} from "@/features/bookmarks/filters";
import type { AudioFilter, ExclusionFilter } from "@/lib/types";

interface BookmarkFiltersProps {
  search: BookmarkSearch;
  /** Applies a change; the page always resets to the first. */
  onChange: (patch: Partial<BookmarkSearch>) => void;
  onReset: () => void;
}

export function BookmarkFilters({ search, onChange, onReset }: BookmarkFiltersProps) {
  const [text, setText] = useState(search.search ?? "");

  function submitSearch(e: FormEvent) {
    e.preventDefault();
    onChange({ search: text.trim() || undefined });
  }

  return (
    <div className="flex flex-col gap-3">
      <form onSubmit={submitSearch} className="flex gap-2" role="search">
        <Input
          type="search"
          aria-label="Search bookmarks"
          placeholder="Search bookmarks…"
          value={text}
          onChange={(e) => setText(e.target.value)}
          className="min-w-0 flex-1"
        />
        <Button type="submit" variant="outline">
          <SearchIcon aria-hidden />
          Search
        </Button>
      </form>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Select
          value={search.audio ?? "any"}
          items={audioOptions}
          onValueChange={(v) => onChange({ audio: v === "any" ? undefined : (v as AudioFilter) })}
        >
          <SelectTrigger aria-label="Filter by audio" className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {audioOptions.map((o) => (
              <SelectItem key={o.value} value={o.value}>
                {o.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select
          value={search.autoGeneration ?? "any"}
          items={exclusionOptions}
          onValueChange={(v) =>
            onChange({ autoGeneration: v === "any" ? undefined : (v as ExclusionFilter) })
          }
        >
          <SelectTrigger aria-label="Filter by auto generation" className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {exclusionOptions.map((o) => (
              <SelectItem key={o.value} value={o.value}>
                {o.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <DateRange
          legend="Added to Readeck"
          idPrefix="added"
          from={search.addedFrom}
          to={search.addedTo}
          onChange={(from, to) => onChange({ addedFrom: from, addedTo: to })}
        />
        <DateRange
          legend="Published"
          idPrefix="published"
          from={search.publishedFrom}
          to={search.publishedTo}
          onChange={(from, to) => onChange({ publishedFrom: from, publishedTo: to })}
        />
      </div>

      {hasActiveFilters(search) && (
        <div>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              setText("");
              onReset();
            }}
          >
            <XIcon aria-hidden />
            Clear filters
          </Button>
        </div>
      )}
    </div>
  );
}

interface DateRangeProps {
  legend: string;
  idPrefix: string;
  from: string | undefined;
  to: string | undefined;
  onChange: (from: string | undefined, to: string | undefined) => void;
}

/** Two optional bounds: only a start means "on or after", only an end "on or before". */
function DateRange({ legend, idPrefix, from, to, onChange }: DateRangeProps) {
  return (
    <fieldset className="flex min-w-0 flex-col gap-1.5">
      <legend className="text-muted-foreground mb-1.5 text-xs font-medium">{legend}</legend>
      <div className="flex items-center gap-2">
        <Label htmlFor={`${idPrefix}-from`} className="sr-only">
          {legend} from
        </Label>
        <Input
          id={`${idPrefix}-from`}
          type="date"
          value={from ?? ""}
          max={to}
          onChange={(e) => onChange(e.target.value || undefined, to)}
          className="min-w-0 flex-1"
        />
        <span className="text-muted-foreground text-xs" aria-hidden>
          to
        </span>
        <Label htmlFor={`${idPrefix}-to`} className="sr-only">
          {legend} to
        </Label>
        <Input
          id={`${idPrefix}-to`}
          type="date"
          value={to ?? ""}
          min={from}
          onChange={(e) => onChange(from, e.target.value || undefined)}
          className="min-w-0 flex-1"
        />
      </div>
    </fieldset>
  );
}
