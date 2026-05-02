import { portalStore } from '../state/portalStore';
import type { PortalProjectSummary } from '../types/portal';

interface ProjectPickerProps {
  projects: PortalProjectSummary[];
  onClose: () => void;
}

export function ProjectPicker({ projects, onClose }: ProjectPickerProps) {
  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section className="project-picker" role="dialog" aria-modal="true" aria-label="选择自动化项目" onMouseDown={(event) => event.stopPropagation()}>
        <header>
          <div>
            <span className="eyebrow">DEFAULT_AUTOMATION_PROJECT_ROOT</span>
            <h2>选择自动化项目</h2>
          </div>
          <button type="button" className="ghost-button" onClick={onClose}>
            关闭
          </button>
        </header>
        {projects.length === 0 ? (
          <div className="empty-state large">自动化根目录下还没有项目。可以直接通过聊天发起生成流程。</div>
        ) : (
          <div className="project-list">
            {projects.map((project) => (
              <button key={project.projectName} type="button" onClick={() => void portalStore.chooseProject(project.projectName)}>
                <strong>{project.projectName}</strong>
                <span>{project.projectDir}</span>
              </button>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

