import { useState } from 'react';
import { portalStore, type PortalState } from '../state/portalStore';
import type { FileTreeNode } from '../types/portal';

interface FilePanelProps {
  state: PortalState;
}

export function FilePanel({ state }: FilePanelProps) {
  const snapshot = state.snapshot;
  const project = snapshot?.activeProject;
  const isActiveSession = snapshot?.sessionId === state.activeSessionId;

  return (
    <aside className="panel file-panel">
      <div className="brand-block">
        <div className="brand-mark">WA</div>
        <div>
          <h1>Web AutoTest</h1>
          <p>Portal 客户端</p>
        </div>
      </div>

      <section className="project-card">
        <span className="eyebrow">当前项目</span>
        {project ? (
          <>
            <strong>{project.projectName}</strong>
            <small className={project.exists ? 'status-ok' : 'status-danger'}>
              {project.exists ? project.projectDir : '项目目录不存在'}
            </small>
          </>
        ) : (
          <>
            <strong>未加载项目</strong>
            <small>可以先打开自动化目录，也可以直接从聊天开始。</small>
          </>
        )}
      </section>

      <section className="file-tree">
        {project && !project.exists ? <div className="empty-state">历史项目已被删除，仍可查看会话内容。</div> : null}
        {!project ? <div className="empty-state">左侧不会自动加载目录。点击底部按钮选择自动化项目。</div> : null}
        {project && project.exists && snapshot.fileTree.length === 0 ? <div className="empty-state">项目目录为空。</div> : null}
        {project && project.exists ? (
          <ul>
            {snapshot.fileTree.map((node) => (
              <FileTreeItem
                key={node.path}
                node={node}
                selectedPath={snapshot.selectedFilePath ?? null}
                disabled={!isActiveSession}
              />
            ))}
          </ul>
        ) : null}
      </section>

      <button className="primary-action" type="button" onClick={() => void portalStore.openProjectPicker()}>
        {project ? '切换项目' : '打开自动化目录'}
      </button>
    </aside>
  );
}

interface FileTreeItemProps {
  node: FileTreeNode;
  selectedPath: string | null;
  disabled: boolean;
}

function FileTreeItem({ node, selectedPath, disabled }: FileTreeItemProps) {
  const [expanded, setExpanded] = useState(true);
  const isDirectory = node.type === 'directory';
  const selected = selectedPath === node.path;

  const handleClick = () => {
    if (isDirectory) {
      setExpanded((current) => !current);
      return;
    }
    if (!disabled) {
      void portalStore.selectFile(selected ? null : node.path);
    }
  };

  return (
    <li>
      <button className={`tree-row ${selected ? 'selected' : ''}`} type="button" onClick={handleClick} disabled={disabled && !isDirectory}>
        <span>{isDirectory ? (expanded ? '▾' : '▸') : '·'}</span>
        <span>{node.name}</span>
      </button>
      {isDirectory && expanded ? (
        <ul className="tree-children">
          {node.children.map((child) => (
            <FileTreeItem key={child.path} node={child} selectedPath={selectedPath} disabled={disabled} />
          ))}
        </ul>
      ) : null}
    </li>
  );
}

