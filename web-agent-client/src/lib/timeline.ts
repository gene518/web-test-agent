import type { CanonicalMessage } from "./message-utils";

export const TIMELINE_TURN_BATCH_SIZE = 20;
export const TIMELINE_TOP_THRESHOLD = 64;
export const TIMELINE_BOTTOM_THRESHOLD = 24;

export type TimelineMessageEntry = {
  message: CanonicalMessage;
  sourceIndex: number;
};

export type TimelineTurn = {
  key: string;
  messages: TimelineMessageEntry[];
};

function messageKey(message: CanonicalMessage, index: number): string {
  return message.id ? String(message.id) : `${message.type}-${index}`;
}

export function groupMessagesIntoTurns(messages: CanonicalMessage[]): TimelineTurn[] {
  const turns: TimelineTurn[] = [];
  for (const [sourceIndex, message] of messages.entries()) {
    if (message.type === "human" || turns.length === 0) {
      turns.push({
        key: `${message.type === "human" ? "turn" : "leading"}:${messageKey(message, sourceIndex)}`,
        messages: [],
      });
    }
    turns[turns.length - 1].messages.push({ message, sourceIndex });
  }
  return turns;
}

export function initialVisibleTurnIndex(
  turnCount: number,
  cachedIndex?: number,
): number {
  if (cachedIndex == null) {
    return Math.max(0, turnCount - TIMELINE_TURN_BATCH_SIZE);
  }
  return Math.min(Math.max(0, cachedIndex), Math.max(0, turnCount - 1));
}

export function previousVisibleTurnIndex(currentIndex: number): number {
  return Math.max(0, currentIndex - TIMELINE_TURN_BATCH_SIZE);
}

export function timelineDistanceFromBottom({
  scrollHeight,
  scrollTop,
  clientHeight,
}: Pick<HTMLElement, "scrollHeight" | "scrollTop" | "clientHeight">): number {
  return Math.max(0, scrollHeight - scrollTop - clientHeight);
}

export function isTimelineNearBottom(
  metrics: Pick<HTMLElement, "scrollHeight" | "scrollTop" | "clientHeight">,
): boolean {
  return timelineDistanceFromBottom(metrics) <= TIMELINE_BOTTOM_THRESHOLD;
}
