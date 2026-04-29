"""Small stdout filter for local LangGraph debugging."""

from __future__ import annotations

import re
import sys
from datetime import UTC, datetime


UTC_TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z")


def localize_utc_timestamps(line: str) -> str:
    def replace(match: re.Match[str]) -> str:
        text = match.group(0)
        try:
            parsed = datetime.fromisoformat(text[:-1]).replace(tzinfo=UTC)
        except ValueError:
            return text
        return parsed.astimezone().replace(tzinfo=None).isoformat(timespec="microseconds")

    return UTC_TIMESTAMP_RE.sub(replace, line)


def main() -> int:
    try:
        for line in sys.stdin:
            if "watchfiles.main" in line:
                continue
            sys.stdout.write(localize_utc_timestamps(line))
            sys.stdout.flush()
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
