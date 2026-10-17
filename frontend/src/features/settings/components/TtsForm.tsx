import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useSaveSettings } from "@/features/settings/hooks";
import { notifications } from "@/lib/notifications";
import type { TtsStatus } from "@/lib/types";

const engineNames: Record<string, string> = {
  "edge-tts": "edge-tts (Microsoft online)",
  kokoro: "Kokoro (local)",
};

// One line on what each engine is, shown under the choice.
const engineNotes: Record<string, string> = {
  "edge-tts":
    "Microsoft's cloud voices over an unofficial endpoint: your article text is sent to Microsoft, and it can break without notice.",
  kokoro: "Small model on your own machine. Fast, and nothing leaves the server.",
};

// What a deployment needs before an engine is offered. edge-tts is always there.
const engineRequirements: Record<string, string> = {
  kokoro: "Kokoro needs the Kokoro image",
};

const toItems = (values: string[], labels: Record<string, string> = {}) =>
  values.map((value) => ({ value, label: labels[value] ?? value }));

function VoiceSelect({
  id,
  label,
  value,
  values,
  onChange,
}: {
  id: string;
  label: string;
  value: string;
  values: string[];
  onChange: (value: string) => void;
}) {
  const items = toItems(values);
  return (
    <div className="flex flex-col gap-2">
      <Label htmlFor={id}>{label}</Label>
      <Select value={value} items={items} onValueChange={(v) => onChange(String(v))}>
        <SelectTrigger id={id} className="w-full sm:max-w-72">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {items.map((o) => (
            <SelectItem key={o.value} value={o.value}>
              {o.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}

export function TtsForm({ status }: { status: TtsStatus }) {
  const save = useSaveSettings();
  const [engine, setEngine] = useState(status.engine);
  const [kokoroVoice, setKokoroVoice] = useState(status.kokoroVoice);
  const [edgeVoice, setEdgeVoice] = useState(status.edgeVoice);
  const engineItems = toItems(status.engines, engineNames);
  const unavailable = Object.keys(engineRequirements).filter((e) => !status.engines.includes(e));

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    save.mutate(
      { tts: { engine, kokoroVoice, edgeVoice } },
      {
        onSuccess: () => notifications.success("Settings saved"),
        onError: (error) =>
          notifications.error("Could not save settings", { description: error.message }),
      },
    );
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Voice</CardTitle>
        <CardDescription>
          The engine and voice used for English articles. Other languages keep their own edge-tts
          voice. A change applies to audio queued afterwards; existing audio and queued jobs keep
          the voice they were created with.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={onSubmit} className="flex flex-col gap-5">
          <div className="flex flex-col gap-2">
            <Label htmlFor="tts-engine">Engine</Label>
            <Select value={engine} items={engineItems} onValueChange={(v) => setEngine(String(v))}>
              <SelectTrigger id="tts-engine" className="w-full sm:max-w-72">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {engineItems.map((o) => (
                  <SelectItem key={o.value} value={o.value}>
                    {o.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {engineNotes[engine] && (
              <p className="text-muted-foreground text-xs">{engineNotes[engine]}</p>
            )}
            {unavailable.length > 0 && (
              <p className="text-muted-foreground text-xs">
                Not available in this deployment:{" "}
                {unavailable.map((e) => engineRequirements[e]).join("; ")}.
              </p>
            )}
          </div>
          {engine === "kokoro" ? (
            <VoiceSelect
              id="kokoro-voice"
              label="Kokoro voice"
              value={kokoroVoice}
              values={status.kokoroVoices}
              onChange={setKokoroVoice}
            />
          ) : (
            <VoiceSelect
              id="edge-voice"
              label="edge-tts voice"
              value={edgeVoice}
              values={status.edgeVoices}
              onChange={setEdgeVoice}
            />
          )}
          <div className="flex flex-col gap-2 sm:flex-row">
            <Button type="submit" className="w-full sm:w-auto" disabled={save.isPending}>
              Save
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}
