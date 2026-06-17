// SSE over fetch + ReadableStream (NOT EventSource): lets us POST a JSON body and
// set headers, and abort cleanly when the user navigates away (CLAUDE.md §6).
import type { ServerEvent } from "./types";

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:7860";

export async function pingHealth(signal?: AbortSignal): Promise<boolean> {
  try {
    const res = await fetch(`${API_URL}/health`, { signal });
    return res.ok;
  } catch {
    return false;
  }
}

interface StreamHandlers {
  onEvent: (event: ServerEvent) => void;
  signal?: AbortSignal;
}

/**
 * POST /research and parse the SSE frames as they arrive. Resolves when the stream
 * ends; rejects on transport error (caller shows retry). Honors `signal` for abort.
 */
export async function streamResearch(topic: string, { onEvent, signal }: StreamHandlers): Promise<void> {
  const res = await fetch(`${API_URL}/research`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify({ topic }),
    signal,
  });

  if (!res.ok || !res.body) {
    throw new Error(`Backend responded ${res.status}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // SSE frames are separated by a blank line.
    let sep: number;
    while ((sep = indexOfFrameBoundary(buffer)) !== -1) {
      const frame = buffer.slice(0, sep);
      buffer = buffer.slice(sep).replace(/^(\r?\n){2}/, "");
      const parsed = parseFrame(frame);
      if (parsed) onEvent(parsed);
    }
  }
}

function indexOfFrameBoundary(buffer: string): number {
  const a = buffer.indexOf("\n\n");
  const b = buffer.indexOf("\r\n\r\n");
  if (a === -1) return b;
  if (b === -1) return a;
  return Math.min(a, b);
}

function parseFrame(frame: string): ServerEvent | null {
  let eventName = "message";
  const dataLines: string[] = [];
  for (const line of frame.split(/\r?\n/)) {
    if (line.startsWith(":")) continue; // heartbeat comment
    if (line.startsWith("event:")) eventName = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  }
  if (dataLines.length === 0) return null;
  try {
    return { event: eventName, data: JSON.parse(dataLines.join("\n")) } as ServerEvent;
  } catch {
    return null;
  }
}
