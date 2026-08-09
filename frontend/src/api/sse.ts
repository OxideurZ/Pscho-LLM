export interface SSEEvent<T = unknown> {
  event: string;
  data: T;
}

export function parseSSEFrames(buffer: string): { events: SSEEvent[]; remainder: string } {
  const normalized = buffer.replace(/\r\n/g, "\n");
  const frames = normalized.split("\n\n");
  const remainder = frames.pop() ?? "";
  const events: SSEEvent[] = [];

  for (const frame of frames) {
    if (!frame.trim()) continue;
    let event = "message";
    const dataLines: string[] = [];
    for (const line of frame.split("\n")) {
      if (line.startsWith(":")) continue;
      if (line.startsWith("event:")) event = line.slice(6).trim();
      if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
    }
    const rawData = dataLines.join("\n");
    if (!rawData) continue;
    events.push({ event, data: JSON.parse(rawData) });
  }
  return { events, remainder };
}

export async function* readSSE(response: Response): AsyncGenerator<SSEEvent> {
  if (!response.ok || !response.body) {
    throw new Error(`HTTP_${response.status}`);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      const parsed = parseSSEFrames(buffer);
      buffer = parsed.remainder;
      for (const event of parsed.events) yield event;
      if (done) break;
    }
  } finally {
    reader.releaseLock();
  }
}

