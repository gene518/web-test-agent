"""H5 客户端使用的只读测试产物预览与下载路由。"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
import anyio
import os
from pathlib import Path
import re
import stat
from urllib.parse import quote, urlencode

from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import HTMLResponse, Response
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

from deep_agent.core.config import get_settings


_LOCATION_SUFFIX = re.compile(r":\d+(?::\d+)?$")
_MAX_QUERY_CHARS = 4096
_MAX_TEXT_PREVIEW_BYTES = 2 * 1024 * 1024
_MAX_IMAGE_PREVIEW_BYTES = 25 * 1024 * 1024
_TEXT_SUFFIXES = frozenset(
    {
        ".css", ".csv", ".html", ".htm", ".js", ".json", ".jsx", ".log",
        ".md", ".mjs", ".mts", ".py", ".toml", ".ts", ".tsx", ".txt",
        ".xml", ".yaml", ".yml",
    }
)
_RASTER_IMAGE_TYPES = {
    ".gif": "image/gif",
    ".ico": "image/x-icon",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}

# H5 端不是通用项目文件浏览器。只公开本产品写入或引用的测试、报告和日志
# 树，避免通过扩展名猜测来维护一个永远不完整的“敏感文件”黑名单。
_PUBLIC_OUTPUT_ROOTS = frozenset(
    {
        "artifacts",
        "logs",
        "playwright-report",
        "reports",
        "test-results",
    }
)
_PUBLIC_OUTPUT_SUFFIXES = frozenset(
    {
        ".css",
        ".csv",
        ".gif",
        ".htm",
        ".html",
        ".ico",
        ".jpeg",
        ".jpg",
        ".js",
        ".json",
        ".log",
        ".md",
        ".mjs",
        ".png",
        ".txt",
        ".webm",
        ".webp",
        ".xml",
        ".zip",
    }
)
_PUBLIC_OUTPUT_STEM = re.compile(
    r"(?:"
    r"[0-9a-f]{8,}"
    r"|(?:attachment|artifact|bundle|console|coverage|data|error|index|image|junit|"
    r"latest|output|playwright|report|resource|results|screenshot|script|style|summary|"
    r"test|trace|video)(?:[-_.][a-z0-9][a-z0-9_.-]*)?"
    r"|\d{6,8}(?:t\d{6}(?:[+-]\d{4})?)?-[a-z0-9][a-z0-9_.-]*"
    r")$",
    re.IGNORECASE,
)
_PUBLIC_TEST_SOURCE_SUFFIX = ".spec.ts"
_PUBLIC_TEST_PLAN_PREFIX = "aaa_"
_PUBLIC_TEST_PLAN_SUFFIX = ".md"
_SCHEDULER_REPORTS_DIR = "scheduler-reports"
_SCHEDULER_LOG_FILE = "scheduler-service.log"


def _not_found() -> HTTPException:
    return HTTPException(404, "产物不存在或不可公开")


def _security_headers() -> dict[str, str]:
    return {
        "Cache-Control": "no-store",
        "Content-Security-Policy": (
            "default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; "
            "base-uri 'none'; form-action 'none'; frame-ancestors 'self'"
        ),
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "SAMEORIGIN",
    }


@dataclass(frozen=True, slots=True)
class _PublicChild:
    path: Path
    is_directory: bool


@dataclass(frozen=True, slots=True)
class _OpenedArtifact:
    path: Path
    descriptor: int
    stat_result: os.stat_result


class _DescriptorFileResponse(Response):
    """从已校验的文件描述符流式响应，避免 FileResponse 再次按路径打开文件。"""

    chunk_size = 64 * 1024

    def __init__(
        self,
        opened: _OpenedArtifact,
        *,
        media_type: str,
        content_disposition_type: str,
        filename: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._descriptor = opened.descriptor
        self.status_code = 200
        self.media_type = media_type
        self.background = None
        response_headers = dict(headers or {})
        response_headers.setdefault("Content-Length", str(opened.stat_result.st_size))
        if filename:
            quoted_name = quote(filename)
            if quoted_name == filename:
                disposition = f'{content_disposition_type}; filename="{filename}"'
            else:
                disposition = (
                    f"{content_disposition_type}; filename*=utf-8''{quoted_name}"
                )
            response_headers.setdefault("Content-Disposition", disposition)
        self.init_headers(response_headers)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await send(
                {
                    "type": "http.response.start",
                    "status": self.status_code,
                    "headers": self.raw_headers,
                }
            )
            if scope["method"].upper() == "HEAD":
                await send(
                    {"type": "http.response.body", "body": b"", "more_body": False}
                )
                return

            while True:
                chunk = await anyio.to_thread.run_sync(
                    os.read, self._descriptor, self.chunk_size
                )
                if not chunk:
                    break
                await send(
                    {
                        "type": "http.response.body",
                        "body": chunk,
                        "more_body": True,
                    }
                )
            await send(
                {"type": "http.response.body", "body": b"", "more_body": False}
            )
        finally:
            await anyio.to_thread.run_sync(os.close, self._descriptor)


class ArtifactResolver:
    """把 H5 产物路径收敛到公开的测试/报告/日志树。"""

    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()

    def resolve(self, raw_path: str, raw_base_dir: str | None = None) -> Path:
        """校验请求路径并返回当前可公开的普通文件或目录。"""

        relative = self._relative_from_request(raw_path, raw_base_dir)
        path, _ = self._validated_path(relative)
        return path

    def revalidate(self, path: Path) -> tuple[Path, os.stat_result]:
        """在读目录、读文本或发送文件前再次按原白名单校验路径。"""

        try:
            relative = path.relative_to(self.root)
        except ValueError:
            raise _not_found() from None
        return self._validated_path(relative)

    def revalidate_file(self, path: Path) -> tuple[Path, os.stat_result]:
        resolved, path_stat = self.revalidate(path)
        if not stat.S_ISREG(path_stat.st_mode):
            raise HTTPException(400, "目录不支持下载")
        return resolved, path_stat

    def revalidate_directory(self, path: Path) -> tuple[Path, os.stat_result]:
        resolved, path_stat = self.revalidate(path)
        if not stat.S_ISDIR(path_stat.st_mode):
            raise HTTPException(400, "文件不是目录")
        return resolved, path_stat

    def open_public_file(self, path: Path) -> _OpenedArtifact:
        """重新校验后用 no-follow 描述符打开文件，并绑定已校验 inode。"""

        resolved, expected_stat = self.revalidate_file(path)
        flags = os.O_RDONLY
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(resolved, flags)
        except OSError:
            raise _not_found() from None

        try:
            opened_stat = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened_stat.st_mode)
                or opened_stat.st_dev != expected_stat.st_dev
                or opened_stat.st_ino != expected_stat.st_ino
            ):
                raise _not_found()
            return _OpenedArtifact(
                path=resolved,
                descriptor=descriptor,
                stat_result=opened_stat,
            )
        except BaseException:
            os.close(descriptor)
            raise

    def public_children(self, directory: Path) -> list[_PublicChild]:
        """列出已按白名单与 no-symlink 规则复核过的目录子项。"""

        resolved_directory, _ = self.revalidate_directory(directory)
        try:
            raw_children = list(resolved_directory.iterdir())
        except OSError:
            raise _not_found() from None

        entries: list[_PublicChild] = []
        for child in raw_children:
            try:
                resolved_child, child_stat = self.revalidate(child)
            except HTTPException:
                continue
            entries.append(
                _PublicChild(
                    path=resolved_child,
                    is_directory=stat.S_ISDIR(child_stat.st_mode),
                )
            )
        return sorted(
            entries,
            key=lambda child: (not child.is_directory, child.path.name.lower()),
        )[:500]

    def is_public_child(self, path: Path) -> bool:
        """保留给调用方的轻量判定入口。"""

        try:
            self.revalidate(path)
        except HTTPException:
            return False
        return True

    def _relative_from_request(
        self, raw_path: str, raw_base_dir: str | None
    ) -> Path:
        normalized_path = self._normalize_query_path(raw_path, "产物路径")
        candidate = Path(normalized_path)
        if candidate.is_absolute():
            relative = self._relative_to_root(candidate)
        else:
            relative = self._base_relative(raw_base_dir) / candidate
        self._ensure_lexically_safe(relative)
        return relative

    def _base_relative(self, raw_base_dir: str | None) -> Path:
        if not raw_base_dir:
            return Path()
        normalized_base = self._normalize_query_path(raw_base_dir, "产物基准目录")
        candidate = Path(normalized_base)
        relative = self._relative_to_root(candidate) if candidate.is_absolute() else candidate
        self._ensure_lexically_safe(relative)
        base_path, base_stat = self._validated_path(relative)
        if not stat.S_ISDIR(base_stat.st_mode):
            raise HTTPException(400, "产物基准路径不是目录")
        return base_path.relative_to(self.root)

    @staticmethod
    def _normalize_query_path(raw_path: str, label: str) -> str:
        if not raw_path or len(raw_path) > _MAX_QUERY_CHARS or "\x00" in raw_path:
            raise HTTPException(400, f"{label}无效")
        normalized = _LOCATION_SUFFIX.sub("", raw_path.strip())
        if not normalized:
            raise HTTPException(400, f"{label}无效")
        return normalized

    def _relative_to_root(self, candidate: Path) -> Path:
        self._ensure_lexically_safe(candidate)
        try:
            raw_relative = candidate.relative_to(self.root)
        except ValueError:
            raw_relative = None
        try:
            canonical_candidate = candidate.resolve(strict=True)
            relative = canonical_candidate.relative_to(self.root)
        except (OSError, ValueError):
            raise _not_found() from None
        # 接受 macOS `/var -> /private/var` 这类操作系统路径别名，但拒绝产物根目录
        # 内部提供的符号链接。
        if raw_relative is not None and raw_relative != relative:
            raise _not_found()
        return relative

    @staticmethod
    def _ensure_lexically_safe(path: Path) -> None:
        if any(part in {"", ".", ".."} for part in path.parts):
            raise _not_found()

    def _validated_path(self, relative: Path) -> tuple[Path, os.stat_result]:
        self._ensure_lexically_safe(relative)
        try:
            root_stat = self.root.stat()
        except OSError:
            raise _not_found() from None
        if not stat.S_ISDIR(root_stat.st_mode):
            raise _not_found()

        current = self.root
        current_stat = root_stat
        for index, part in enumerate(relative.parts):
            current = current / part
            try:
                current_stat = current.lstat()
            except OSError:
                raise _not_found() from None
            if stat.S_ISLNK(current_stat.st_mode):
                raise _not_found()
            if index < len(relative.parts) - 1 and not stat.S_ISDIR(current_stat.st_mode):
                raise _not_found()

        if not (stat.S_ISDIR(current_stat.st_mode) or stat.S_ISREG(current_stat.st_mode)):
            raise _not_found()
        self._ensure_public(relative, current_stat)
        return current, current_stat

    @staticmethod
    def _ensure_public(relative: Path, path_stat: os.stat_result) -> None:
        parts = relative.parts
        is_directory = stat.S_ISDIR(path_stat.st_mode)
        if any(not part or part.startswith(".") for part in parts):
            raise _not_found()

        # `/data/projects` itself lists project directories. It deliberately does not
        # expose direct files, so a project-level .env, id_rsa, or config JSON cannot
        # become public merely because it has a familiar suffix.
        if not parts:
            if not is_directory:
                raise _not_found()
            return
        if len(parts) == 1:
            if not is_directory:
                raise _not_found()
            return

        artifact_root = parts[1].lower()
        remainder = parts[2:]
        if artifact_root == "test_case":
            ArtifactResolver._ensure_public_test_case(remainder, path_stat)
            return
        if artifact_root in _PUBLIC_OUTPUT_ROOTS:
            ArtifactResolver._ensure_public_output(remainder, path_stat)
            return
        raise _not_found()

    @staticmethod
    def _ensure_public_test_case(
        remainder: tuple[str, ...], path_stat: os.stat_result
    ) -> None:
        if not remainder:
            if not stat.S_ISDIR(path_stat.st_mode):
                raise _not_found()
            return

        if remainder[0].lower() == _SCHEDULER_REPORTS_DIR:
            ArtifactResolver._ensure_public_output(remainder[1:], path_stat)
            return
        if len(remainder) == 1 and remainder[0].lower() == _SCHEDULER_LOG_FILE:
            if stat.S_ISREG(path_stat.st_mode):
                return
            raise _not_found()
        if stat.S_ISDIR(path_stat.st_mode):
            return

        file_name = remainder[-1].lower()
        if file_name.endswith(_PUBLIC_TEST_SOURCE_SUFFIX) or (
            file_name.startswith(_PUBLIC_TEST_PLAN_PREFIX)
            and file_name.endswith(_PUBLIC_TEST_PLAN_SUFFIX)
        ):
            return
        raise _not_found()

    @staticmethod
    def _ensure_public_output(
        remainder: tuple[str, ...], path_stat: os.stat_result
    ) -> None:
        if stat.S_ISDIR(path_stat.st_mode):
            return
        if not remainder:
            raise _not_found()
        output_file = Path(remainder[-1])
        if (
            output_file.suffix.lower() in _PUBLIC_OUTPUT_SUFFIXES
            and _PUBLIC_OUTPUT_STEM.fullmatch(output_file.stem)
        ):
            return
        raise _not_found()


def _artifact_query(path: Path, root: Path) -> str:
    return urlencode({"path": path.relative_to(root).as_posix()})


def _page(title: str, body: str) -> HTMLResponse:
    document = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)}</title>
  <style>
    :root {{ color-scheme: light dark; font-family: ui-sans-serif, system-ui, sans-serif; }}
    body {{ margin: 0 auto; max-width: 1100px; padding: 24px; line-height: 1.55; }}
    header {{ display: flex; align-items: center; justify-content: space-between; gap: 16px; }}
    h1 {{ margin: 0; font-size: 20px; overflow-wrap: anywhere; }}
    a {{ color: #167553; }}
    .download {{ border: 1px solid #8b949e; border-radius: 6px; padding: 7px 11px; text-decoration: none; white-space: nowrap; }}
    .meta {{ color: #667085; font-size: 13px; }}
    pre {{ overflow: auto; border: 1px solid #d0d5dd; border-radius: 6px; padding: 16px; white-space: pre-wrap; overflow-wrap: anywhere; }}
    img {{ display: block; max-width: 100%; max-height: 78vh; margin: 20px auto; object-fit: contain; }}
    ul {{ padding: 0; list-style: none; border-top: 1px solid #d0d5dd; }}
    li {{ border-bottom: 1px solid #d0d5dd; }}
    li a {{ display: block; padding: 10px 4px; text-decoration: none; overflow-wrap: anywhere; }}
  </style>
</head>
<body>{body}</body>
</html>"""
    return HTMLResponse(document, headers=_security_headers())


async def _read_opened_text(opened: _OpenedArtifact) -> str:
    try:
        data = bytearray()
        while len(data) <= _MAX_TEXT_PREVIEW_BYTES:
            chunk = await anyio.to_thread.run_sync(
                os.read,
                opened.descriptor,
                min(64 * 1024, _MAX_TEXT_PREVIEW_BYTES + 1 - len(data)),
            )
            if not chunk:
                break
            data.extend(chunk)
        return bytes(data).decode("utf-8", errors="replace")
    finally:
        await anyio.to_thread.run_sync(os.close, opened.descriptor)


def _descriptor_response(
    opened: _OpenedArtifact,
    *,
    media_type: str,
    content_disposition_type: str,
    filename: str | None = None,
) -> _DescriptorFileResponse:
    return _DescriptorFileResponse(
        opened,
        media_type=media_type,
        content_disposition_type=content_disposition_type,
        filename=filename,
        headers=_security_headers(),
    )


def build_artifact_http_app(root: Path | None = None) -> Starlette:
    """构建由 LangGraph 自定义 HTTP 配置挂载的只读路由应用。"""

    resolver = ArtifactResolver(
        root or get_settings().resolved_default_automation_project_root
    )

    async def preview(request: Request) -> Response:
        artifact = await anyio.to_thread.run_sync(
            resolver.resolve,
            request.query_params.get("path", ""),
            request.query_params.get("base_dir"),
        )
        artifact, artifact_stat = await anyio.to_thread.run_sync(
            resolver.revalidate, artifact
        )
        title = artifact.name or artifact.as_posix()
        if stat.S_ISDIR(artifact_stat.st_mode):
            entries = await anyio.to_thread.run_sync(resolver.public_children, artifact)
            items = "".join(
                f'<li><a href="./preview?{_artifact_query(child.path, resolver.root)}">'
                f"{'目录' if child.is_directory else '文件'} · {escape(child.path.name)}"
                "</a></li>"
                for child in entries
            )
            return _page(
                title,
                f"<header><h1>{escape(title)}</h1></header>"
                f'<p class="meta">目录 · 显示 {len(entries)} 项</p><ul>{items}</ul>',
            )

        opened = await anyio.to_thread.run_sync(resolver.open_public_file, artifact)
        size = opened.stat_result.st_size
        header = (
            f"<header><h1>{escape(title)}</h1>"
            f'<a class="download" href="./download?{_artifact_query(artifact, resolver.root)}">下载</a></header>'
            f'<p class="meta">{size} bytes</p>'
        )
        suffix = artifact.suffix.lower()
        if suffix in _TEXT_SUFFIXES and size <= _MAX_TEXT_PREVIEW_BYTES:
            content = await _read_opened_text(opened)
            return _page(title, f"{header}<pre>{escape(content)}</pre>")
        await anyio.to_thread.run_sync(os.close, opened.descriptor)
        if suffix in _RASTER_IMAGE_TYPES and size <= _MAX_IMAGE_PREVIEW_BYTES:
            query = _artifact_query(artifact, resolver.root)
            return _page(
                title,
                f'{header}<img src="./raw?{query}" alt="{escape(title)}">',
            )
        return _page(
            title,
            f"{header}<p>该文件类型或大小不支持在线预览，请下载后查看。</p>",
        )

    async def download(request: Request) -> Response:
        artifact = await anyio.to_thread.run_sync(
            resolver.resolve,
            request.query_params.get("path", ""),
            request.query_params.get("base_dir"),
        )
        opened = await anyio.to_thread.run_sync(resolver.open_public_file, artifact)
        return _descriptor_response(
            opened,
            media_type="application/octet-stream",
            content_disposition_type="attachment",
            filename=opened.path.name,
        )

    async def raw_image(request: Request) -> Response:
        artifact = await anyio.to_thread.run_sync(
            resolver.resolve, request.query_params.get("path", "")
        )
        opened = await anyio.to_thread.run_sync(resolver.open_public_file, artifact)
        media_type = _RASTER_IMAGE_TYPES.get(opened.path.suffix.lower())
        if media_type is None or opened.stat_result.st_size > _MAX_IMAGE_PREVIEW_BYTES:
            await anyio.to_thread.run_sync(os.close, opened.descriptor)
            raise HTTPException(404, "图片预览不可用")
        return _descriptor_response(
            opened,
            media_type=media_type,
            content_disposition_type="inline",
        )

    return Starlette(
        routes=[
            Route("/artifacts/preview", preview, methods=["GET"]),
            Route("/artifacts/download", download, methods=["GET"]),
            Route("/artifacts/raw", raw_image, methods=["GET"]),
        ]
    )


__all__ = ["ArtifactResolver", "build_artifact_http_app"]
