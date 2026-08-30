from __future__ import annotations

from pathlib import Path

import pytest

from deep_agent.core.autotest_project_directory import resolve_autotest_project_dir


def _template_dir(root: Path) -> Path:
    template = root / "template"
    template.mkdir()
    (template / "package.json").write_text("{}\n", encoding="utf-8")
    return template


@pytest.mark.parametrize(
    "project_name", ["../outside", "../../outside", "/tmp/outside", r"..\outside"]
)
def test_project_name_cannot_escape_automation_root(
    tmp_path: Path, project_name: str
) -> None:
    automation_root = tmp_path / "projects"

    with pytest.raises(RuntimeError, match="单个路径段"):
        resolve_autotest_project_dir(
            automation_root=automation_root,
            bundled_template_dir=_template_dir(tmp_path),
            project_name=project_name,
            raw_project_dir=None,
            missing_project_name_error="missing",
        )


def test_relative_project_dir_cannot_escape_automation_root(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="不能逃逸"):
        resolve_autotest_project_dir(
            automation_root=tmp_path / "projects",
            bundled_template_dir=_template_dir(tmp_path),
            project_name=None,
            raw_project_dir="../../outside",
            missing_project_name_error="missing",
        )


def test_explicit_absolute_project_dir_remains_supported(tmp_path: Path) -> None:
    external_project = tmp_path / "external" / "project"

    resolved = resolve_autotest_project_dir(
        automation_root=tmp_path / "projects",
        bundled_template_dir=_template_dir(tmp_path),
        project_name=None,
        raw_project_dir=external_project,
        missing_project_name_error="missing",
    )

    assert resolved == external_project.resolve()
    assert (resolved / "package.json").is_file()
