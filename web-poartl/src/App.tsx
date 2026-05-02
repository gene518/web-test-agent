import { useEffect } from 'react';
import { FilePanel } from './components/FilePanel';
import { HistoryPanel } from './components/HistoryPanel';
import { MainPanel } from './components/MainPanel';
import { ProjectPicker } from './components/ProjectPicker';
import { usePortalStore } from './hooks/usePortalStore';
import { usePortalStream } from './hooks/usePortalStream';
import { portalStore } from './state/portalStore';

export function App() {
  const state = usePortalStore();
  usePortalStream(state.activeSessionId);

  useEffect(() => {
    void portalStore.bootstrap();
  }, []);

  return (
    <main className="portal-shell">
      <FilePanel state={state} />
      <MainPanel state={state} />
      <HistoryPanel state={state} />
      {state.projectPickerOpen ? <ProjectPicker projects={state.projects} onClose={portalStore.closeProjectPicker} /> : null}
    </main>
  );
}

