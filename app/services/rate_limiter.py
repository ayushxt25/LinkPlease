import time

import redis

from app.core.config import settings


class RedisRateLimiter:
    script = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4]
redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
local count = redis.call('ZCARD', key)
if count >= limit then
  local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
  return math.max(1, math.ceil((tonumber(oldest[2]) + window - now) / 1000))
end
redis.call('ZADD', key, now, member)
redis.call('PEXPIRE', key, window)
return 0
"""

    def __init__(self, redis_url: str | None = None, limit: int = 10, window_seconds: int = 60) -> None:
        self.client = redis.Redis.from_url(redis_url or settings.redis_url)
        self.limit = limit
        self.window_ms = window_seconds * 1000

    def acquire(self, member: str) -> int:
        now_ms = int(time.time() * 1000)
        return int(
            self.client.eval(
                self.script,
                1,
                "pseudogram:dm_send_rate",
                now_ms,
                self.window_ms,
                self.limit,
                member,
            )
        )
