from __future__ import annotations

import json
from pathlib import Path

from deep_agent.scheduler.deployment_state import SchedulerDeploymentState


def test_scheduler_deployment_state_observes_gate_and_writes_atomically(
    tmp_path: Path,
) -> None:
    status = tmp_path / "state" / "scheduler-status.json"
    maintenance = tmp_path / "state" / "maintenance.json"
    deployment = SchedulerDeploymentState(
        status_path=status,
        maintenance_path=maintenance,
    )

    deployment.publish(active_run="demo/smoke", pending_runs=2)
    first = json.loads(status.read_text(encoding="utf-8"))
    assert first["active_run"] == "demo/smoke"
    assert first["pending_runs"] == 2
    assert first["maintenance"] is False

    maintenance.write_text("{}", encoding="utf-8")
    assert deployment.maintenance_active() is True
    deployment.publish(active_run=None, pending_runs=1, online=False)
    final = json.loads(status.read_text(encoding="utf-8"))
    assert final["online"] is False
    assert final["active_run"] is None
    assert final["pending_runs"] == 1
    assert final["maintenance"] is True
