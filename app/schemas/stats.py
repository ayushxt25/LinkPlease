from pydantic import BaseModel


class StatsResponse(BaseModel):
    sent: int = 0
    failed: int = 0
    queued: int = 0
    duplicates_blocked: int = 0
