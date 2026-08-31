"""Docker 更新期间供 scheduler 与 updater 共享的轻量状态。"""

from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from pathlib import Path


class SchedulerDeploymentState:
    """读取维护闸门并原子发布 scheduler 活动状态。"""

    def __init__(
        self,
        *,
        status_path: Path | None = None,
        maintenance_path: Path | None = None,
    ) -> None:
        self.status_path = status_path
        self.maintenance_path = maintenance_path

    @classmethod
    def from_environment(cls) -> "SchedulerDeploymentState":
        status_value = os.environ.get("SCHEDULER_STATUS_FILE", "").strip()
        maintenance_value = os.environ.get("SCHEDULER_MAINTENANCE_FILE", "").strip()
        return cls(
            status_path=Path(status_value) if status_value else None,
            maintenance_path=Path(maintenance_value) if maintenance_value else None,
        )

    def maintenance_active(self) -> bool:
        return bool(self.maintenance_path and self.maintenance_path.is_file())

    def publish(
        self,
        *,
        active_run: str | None,
        pending_runs: int,
        online: bool = True,
    ) -> None:
        if self.status_path is None:
            return
        payload = {
            "online": online,
            "active_run": active_run,
            "pending_runs": pending_runs,
            "maintenance": self.maintenance_active(),
            "updated_at": datetime.now(UTC).isoformat(),
        }
        self.status_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.status_path.with_name(
            f".{self.status_path.name}.{os.getpid()}.tmp"
        )
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.status_path)


__all__ = ["SchedulerDeploymentState"]
