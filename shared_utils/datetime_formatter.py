from datetime import datetime, timezone
from typing import Optional


def to_utc_z(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)

    return value.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
