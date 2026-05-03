"""最小可用的五段 Cron 表达式解析与匹配。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


CRON_FIELD_RANGES: dict[str, tuple[int, int]] = {
    "minute": (0, 59),
    "hour": (0, 23),
    "day_of_month": (1, 31),
    "month": (1, 12),
    "day_of_week": (0, 7),
}


@dataclass(frozen=True, slots=True)
class CronField:
    """Cron 单字段的解析结果。"""

    values: frozenset[int]
    is_wildcard: bool


@dataclass(frozen=True, slots=True)
class CronExpression:
    """五段 Cron 表达式。"""

    minute: CronField
    hour: CronField
    day_of_month: CronField
    month: CronField
    day_of_week: CronField

    @classmethod
    def parse(cls, expression: str) -> "CronExpression":
        """解析五段 Cron 表达式。"""

        parts = expression.split()
        if len(parts) != 5:
            raise ValueError("Cron 表达式必须正好包含 5 段。")

        minute = _parse_field(parts[0], field_name="minute")
        hour = _parse_field(parts[1], field_name="hour")
        day_of_month = _parse_field(parts[2], field_name="day_of_month")
        month = _parse_field(parts[3], field_name="month")
        day_of_week = _parse_field(parts[4], field_name="day_of_week")
        return cls(
            minute=minute,
            hour=hour,
            day_of_month=day_of_month,
            month=month,
            day_of_week=day_of_week,
        )

    def matches(self, scheduled_time: datetime) -> bool:
        """判断某个时间点是否命中当前 Cron 表达式。"""

        cron_weekday = (scheduled_time.weekday() + 1) % 7
        minute_matches = scheduled_time.minute in self.minute.values
        hour_matches = scheduled_time.hour in self.hour.values
        month_matches = scheduled_time.month in self.month.values
        day_of_month_matches = scheduled_time.day in self.day_of_month.values
        day_of_week_matches = cron_weekday in self.day_of_week.values
        if 7 in self.day_of_week.values and cron_weekday == 0:
            day_of_week_matches = True

        if not (minute_matches and hour_matches and month_matches):
            return False

        if not self.day_of_month.is_wildcard and not self.day_of_week.is_wildcard:
            return day_of_month_matches or day_of_week_matches
        if not self.day_of_month.is_wildcard:
            return day_of_month_matches
        if not self.day_of_week.is_wildcard:
            return day_of_week_matches
        return True


def validate_cron_expression(expression: str) -> None:
    """仅校验 Cron 表达式是否合法。"""

    CronExpression.parse(expression)


def _parse_field(field_text: str, *, field_name: str) -> CronField:
    """解析单个 Cron 字段。"""

    minimum_value, maximum_value = CRON_FIELD_RANGES[field_name]
    if field_text == "*":
        return CronField(
            values=frozenset(range(minimum_value, maximum_value + 1)),
            is_wildcard=True,
        )

    values: set[int] = set()
    for token in field_text.split(","):
        token = token.strip()
        if not token:
            raise ValueError(f"Cron 字段 `{field_name}` 包含空片段。")
        values.update(
            _expand_token(
                token,
                minimum_value=minimum_value,
                maximum_value=maximum_value,
                field_name=field_name,
            )
        )

    if not values:
        raise ValueError(f"Cron 字段 `{field_name}` 未解析出任何可用值。")
    return CronField(values=frozenset(values), is_wildcard=False)


def _expand_token(
    token: str,
    *,
    minimum_value: int,
    maximum_value: int,
    field_name: str,
) -> set[int]:
    """展开一个 Cron token，例如 `*/5`、`1-10/2`、`7`。"""

    if "/" in token:
        base_text, step_text = token.split("/", 1)
        if not step_text.isdigit():
            raise ValueError(f"Cron 字段 `{field_name}` 的步长非法：`{token}`。")
        step = int(step_text)
        if step <= 0:
            raise ValueError(f"Cron 字段 `{field_name}` 的步长必须大于 0：`{token}`。")
    else:
        base_text = token
        step = 1

    if base_text == "*":
        start = minimum_value
        end = maximum_value
    elif "-" in base_text:
        start_text, end_text = base_text.split("-", 1)
        start = _parse_int(start_text, field_name=field_name, token=token)
        end = _parse_int(end_text, field_name=field_name, token=token)
        if start > end:
            raise ValueError(f"Cron 字段 `{field_name}` 的范围非法：`{token}`。")
    else:
        value = _parse_int(base_text, field_name=field_name, token=token)
        _validate_range(value, minimum_value=minimum_value, maximum_value=maximum_value, field_name=field_name, token=token)
        return {value}

    _validate_range(start, minimum_value=minimum_value, maximum_value=maximum_value, field_name=field_name, token=token)
    _validate_range(end, minimum_value=minimum_value, maximum_value=maximum_value, field_name=field_name, token=token)
    return set(range(start, end + 1, step))


def _parse_int(value_text: str, *, field_name: str, token: str) -> int:
    """把 token 中的数值部分解析成整数。"""

    if not value_text.isdigit():
        raise ValueError(f"Cron 字段 `{field_name}` 存在非法数字：`{token}`。")
    return int(value_text)


def _validate_range(
    value: int,
    *,
    minimum_value: int,
    maximum_value: int,
    field_name: str,
    token: str,
) -> None:
    """校验数值是否处于该字段允许的范围内。"""

    if value < minimum_value or value > maximum_value:
        raise ValueError(
            f"Cron 字段 `{field_name}` 超出允许范围 {minimum_value}-{maximum_value}：`{token}`。"
        )


__all__ = ["CronExpression", "validate_cron_expression"]
