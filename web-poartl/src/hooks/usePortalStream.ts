import { useEffect } from 'react';
import { portalApi } from '../api/client';
import { portalStore } from '../state/portalStore';
import type { PortalEvent, PortalEventType } from '../types/portal';

const EVENT_TYPES: PortalEventType[] = [
  'session_created',
  'message_started',
  'node_updated',
  'tool_updated',
  'project_changed',
  'message_completed',
  'message_failed',
];

export function usePortalStream(sessionId: string | null): void {
  useEffect(() => {
    if (!sessionId) {
      portalStore.setConnectionStatus('idle');
      return undefined;
    }

    portalStore.setConnectionStatus('connecting');
    const source = new EventSource(portalApi.streamUrl(sessionId));

    source.onopen = () => {
      portalStore.setConnectionStatus('open');
    };
    source.onerror = () => {
      portalStore.setConnectionStatus('error');
    };

    const listeners = EVENT_TYPES.map((eventType) => {
      const listener = (message: MessageEvent<string>) => {
        const event = JSON.parse(message.data) as PortalEvent;
        portalStore.applyEvent(event);
      };
      source.addEventListener(eventType, listener);
      return { eventType, listener };
    });

    return () => {
      listeners.forEach(({ eventType, listener }) => {
        source.removeEventListener(eventType, listener);
      });
      source.close();
      portalStore.setConnectionStatus('closed');
    };
  }, [sessionId]);
}

