from redis.asyncio import Redis

from app.core.config import settings

redis_client: Redis | None = None


def init_redis(url: str | None = None) -> Redis:
    global redis_client
    if not redis_client:
        redis_url = url or getattr(settings, "REDIS_URL", None) or getattr(settings, "RADIS_URL", "redis://redis:6379/0")
        redis_client = Redis.from_url(redis_url, encoding="utf-8", decode_responses=True)
    return redis_client


def get_redis() -> Redis:
    global redis_client
    if not redis_client:
        init_redis()
    return redis_client

