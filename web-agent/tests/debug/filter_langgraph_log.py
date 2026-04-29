"""Small stdout filter for local LangGraph debugging."""

from __future__ import annotations

import posixpath
import re
import sys
from datetime import UTC, datetime


UTC_TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z")
WATCHFILES_CHANGE_MARKER = "WatchFiles detected changes in "
WATCHFILES_RELOADING_MARKER = "Reloading..."


def localize_utc_timestamps(line: str) -> str:
    def replace(match: re.Match[str]) -> str:
        text = match.group(0)
        try:
            parsed = datetime.fromisoformat(text[:-1]).replace(tzinfo=UTC)
        except ValueError:
            return text
        return parsed.astimezone().replace(tzinfo=None).isoformat(timespec="microseconds")

    return UTC_TIMESTAMP_RE.sub(replace, line)


def _common_parent_dir(paths: list[str]) -> str | None:
    if not paths:
        return None
    try:
        common = posixpath.commonpath(paths)
    except ValueError:
        return None
    if not common or common in (".", "/"):
        return None
    if posixpath.splitext(common)[1]:
        common = posixpath.dirname(common)
    return common or None


def shorten_watchfiles_change_noise(line: str) -> str:
    if WATCHFILES_CHANGE_MARKER not in line:
        return line

    marker_start = line.find(WATCHFILES_CHANGE_MARKER)
    list_start = marker_start + len(WATCHFILES_CHANGE_MARKER)
    reloading_start = line.find(WATCHFILES_RELOADING_MARKER, list_start)
    if reloading_start == -1:
        return line

    paths = re.findall(r"'([^']+)'", line[list_start:reloading_start])
    if not paths:
        return line

    common_parent = _common_parent_dir(paths)
    scope = f" under '{common_parent}/'" if common_parent else ""
    summary = f"{len(paths)} paths{scope} (suppressed; e.g. '{paths[0]}')"

    prefix = line[:list_start]
    suffix = line[reloading_start:]
    return f"{prefix}{summary}. {suffix}"


def main() -> int:
    try:
        for line in sys.stdin:
            if "watchfiles.main" in line:
                continue
            line = shorten_watchfiles_change_noise(line)
            sys.stdout.write(localize_utc_timestamps(line))
            sys.stdout.flush()
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
