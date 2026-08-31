import { describe, expect, it } from "vitest";
import type { CanonicalMessage } from "./message-utils";
import {
  groupMessagesIntoTurns,
  initialVisibleTurnIndex,
  isTimelineNearBottom,
  previousVisibleTurnIndex,
  timelineDistanceFromBottom,
} from "./timeline";

function message(
  id: string,
  type: CanonicalMessage["type"],
  content = id,
): CanonicalMessage {
  return { id, type, content } as CanonicalMessage;
}

describe("message timeline turns", () => {
  it("keeps each human message and all following AI/tool messages in one turn", () => {
    const turns = groupMessagesIntoTurns([
      message("h1", "human"),
      message("a1", "ai"),
      message("t1", "tool"),
      message("a2", "ai"),
      message("h2", "human"),
      message("a3", "ai"),
    ]);

    expect(turns.map((turn) => turn.key)).toEqual(["turn:h1", "turn:h2"]);
    expect(turns.map((turn) => turn.messages.map(({ message: item }) => item.id))).toEqual([
      ["h1", "a1", "t1", "a2"],
      ["h2", "a3"],
    ]);
  });

  it("keeps leading system output in a stable leading turn", () => {
    const turns = groupMessagesIntoTurns([
      message("a0", "ai"),
      message("t0", "tool"),
      message("h1", "human"),
    ]);
    expect(turns.map((turn) => turn.key)).toEqual(["leading:a0", "turn:h1"]);
  });
});

describe("message timeline paging and following", () => {
  it("starts with the latest 20 turns and reveals 20 earlier turns per batch", () => {
    expect(initialVisibleTurnIndex(120)).toBe(100);
    expect(previousVisibleTurnIndex(100)).toBe(80);
    expect(previousVisibleTurnIndex(10)).toBe(0);
    expect(initialVisibleTurnIndex(120, 40)).toBe(40);
  });

  it("treats content within 24px of the bottom as following", () => {
    expect(timelineDistanceFromBottom({ scrollHeight: 1000, scrollTop: 576, clientHeight: 400 })).toBe(24);
    expect(isTimelineNearBottom({ scrollHeight: 1000, scrollTop: 576, clientHeight: 400 })).toBe(true);
    expect(isTimelineNearBottom({ scrollHeight: 1000, scrollTop: 575, clientHeight: 400 })).toBe(false);
  });
});
