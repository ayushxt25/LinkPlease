from typing import Literal
from datetime import datetime

from pydantic import BaseModel, Field


class CommentAuthor(BaseModel):
    user_id: str = Field(..., min_length=1)
    username: str = Field(..., min_length=1)


class CommentData(BaseModel):
    comment_id: str = Field(..., min_length=1)
    post_id: str | None = None
    text: str | None = None
    created_at: datetime | None = None
    from_user: CommentAuthor | None = Field(default=None, alias="from")


class WebhookPayload(BaseModel):
    event_id: str = Field(..., min_length=1)
    event_type: Literal["comment.created", "comment.deleted"]
    sent_at: datetime | None = None
    data: CommentData
