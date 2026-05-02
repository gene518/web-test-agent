import { useSyncExternalStore } from 'react';
import { portalStore } from '../state/portalStore';

export function usePortalStore() {
  return useSyncExternalStore(portalStore.subscribe, portalStore.getSnapshot, portalStore.getSnapshot);
}

