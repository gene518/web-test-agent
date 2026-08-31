"""容器更新服务共享的状态、镜像和命令辅助函数。"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any


COMMIT_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
IMAGE_ID_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
IMAGE_KINDS = ("agent", "web", "updater")
ALLOWED_GITHUB_REPOSITORY = "gene518/web-test-agent"
ALLOWED_CONTAINER_WORKFLOW = "ci.yml"
ALLOWED_CONTAINER_PUBLISH_JOB = "发布 H5 容器镜像"
ALLOWED_IMAGE_PREFIX = "ghcr.io/gene518/web-test-agent"
ALLOWED_COSIGN_IDENTITY = (
    "https://github.com/gene518/web-test-agent/.github/workflows/ci.yml@refs/heads/main"
)


def require_commit_sha(value: str) -> str:
    """只接受 GitHub 提交使用的完整小写 SHA。"""

    normalized = value.strip().lower()
    if not COMMIT_SHA_PATTERN.fullmatch(normalized):
        raise ValueError("target revision must be a full 40-character commit SHA")
    return normalized


def image_references(prefix: str, revision: str) -> dict[str, str]:
    """为固定服务生成不可变 SHA tag，不接受客户端自定义镜像。"""

    normalized_sha = require_commit_sha(revision)
    normalized_prefix = prefix.strip().rstrip("/")
    if normalized_prefix != ALLOWED_IMAGE_PREFIX:
        raise ValueError("image prefix is not in the updater allowlist")
    return {
        kind: f"{normalized_prefix}-{kind}:sha-{normalized_sha}" for kind in IMAGE_KINDS
    }


def require_image_digest(
    reference: str,
    kind: str,
    *,
    prefix: str = ALLOWED_IMAGE_PREFIX,
) -> str:
    """校验固定服务的完整 OCI digest 引用。"""

    if kind not in IMAGE_KINDS:
        raise ValueError(f"unknown image kind: {kind}")
    if prefix != ALLOWED_IMAGE_PREFIX:
        raise ValueError("image prefix is not in the updater allowlist")
    expected = re.compile(
        rf"^{re.escape(prefix)}-{re.escape(kind)}@sha256:[0-9a-f]{{64}}$"
    )
    normalized = reference.strip()
    if not expected.fullmatch(normalized):
        raise ValueError(f"invalid immutable {kind} image reference")
    return normalized


def require_image_id(value: str) -> str:
    """只接受 Docker 返回的完整内容寻址镜像 ID。"""

    normalized = value.strip()
    if not IMAGE_ID_PATTERN.fullmatch(normalized):
        raise ValueError("invalid immutable Docker image ID")
    return normalized


def verify_signed_image(reference: str, revision: str, kind: str) -> None:
    """校验镜像来源、提交标签和 GitHub Actions keyless 签名。"""

    immutable_reference = require_image_digest(reference, kind)
    normalized_revision = require_commit_sha(revision)
    source = run_command(
        [
            "docker",
            "image",
            "inspect",
            immutable_reference,
            "--format",
            '{{ index .Config.Labels "org.opencontainers.image.source" }}',
        ]
    )
    image_revision = run_command(
        [
            "docker",
            "image",
            "inspect",
            immutable_reference,
            "--format",
            '{{ index .Config.Labels "org.opencontainers.image.revision" }}',
        ]
    )
    expected_source = f"https://github.com/{ALLOWED_GITHUB_REPOSITORY}"
    if source != expected_source or image_revision != normalized_revision:
        raise RuntimeError(
            f"image labels do not match the updater allowlist: {immutable_reference}"
        )
    run_command(
        [
            "cosign",
            "verify",
            "--certificate-identity",
            ALLOWED_COSIGN_IDENTITY,
            "--certificate-oidc-issuer",
            "https://token.actions.githubusercontent.com",
            immutable_reference,
        ],
        timeout_seconds=120,
    )


def read_json(path: Path, default: Any) -> Any:
    """读取 JSON；文件不存在时返回调用方给定的默认值。"""

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def atomic_write_json(path: Path, payload: Any) -> None:
    """在同一目录内原子替换 JSON 状态文件。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def run_command(
    arguments: list[str],
    *,
    input_text: str | None = None,
    timeout_seconds: float = 900,
) -> str:
    """以参数数组运行受控命令，并把失败输出转换为异常。"""

    result = subprocess.run(
        arguments,
        input=input_text,
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "command failed").strip()
        raise RuntimeError(f"command failed ({result.returncode}): {detail[:2000]}")
    return result.stdout.strip()
