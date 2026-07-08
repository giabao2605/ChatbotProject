export type SseEvent = {
  event: string;
  data: unknown;
};

export function parseSseBuffer(buffer: string): { events: SseEvent[]; rest: string } {
  const chunks = buffer.split("\n\n");
  const rest = chunks.pop() ?? "";
  const events: SseEvent[] = [];

  for (const raw of chunks) {
    let event = "message";
    let dataText = "";
    for (const line of raw.split("\n")) {
      if (line.startsWith("event:")) {
        event = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        dataText += line.slice(5).trim();
      }
    }
    if (!dataText) continue;
    events.push({ event, data: JSON.parse(dataText) });
  }

  return { events, rest };
}
