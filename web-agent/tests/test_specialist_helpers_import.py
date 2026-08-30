from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_specialist_helpers_can_be_imported_before_agent_package() -> None:
    project_dir = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from deep_agent.helpers.specialist_helpers "
                "import SpecialistDisplayMixin, bundled_demo_template_dir; "
                "assert SpecialistDisplayMixin; "
                "assert bundled_demo_template_dir().is_dir()"
            ),
        ],
        cwd=project_dir,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
