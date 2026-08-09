import { describe, expect, it } from "vitest";
import { parseSSEFrames } from "./sse";

describe("parseSSEFrames", () => {
  it("retains incomplete chunks and parses multiple frames", () => {
    const first = parseSSEFrames('event: delta\ndata: {"text":"Bon');
    expect(first.events).toEqual([]);
    const second = parseSSEFrames(first.remainder + 'jour"}\n\nevent: done\ndata: {}\n\n');
    expect(second.events).toEqual([
      { event: "delta", data: { text: "Bonjour" } },
      { event: "done", data: {} },
    ]);
    expect(second.remainder).toBe("");
  });

  it("supports CRLF, comments, and multi-line data", () => {
    const parsed = parseSSEFrames(': ping\r\nevent: note\r\ndata: [1,\r\ndata: 2]\r\n\r\n');
    expect(parsed.events).toEqual([{ event: "note", data: [1, 2] }]);
  });
});

