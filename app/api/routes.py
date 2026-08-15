from uuid import uuid4

from fastapi import APIRouter, status

from app.schemas.rule import RuleCreate, RuleResponse
from app.schemas.stats import StatsResponse
from app.schemas.webhook import WebhookPayload

router = APIRouter()

rules: dict[str, RuleResponse] = {}


@router.get("/health", status_code=status.HTTP_200_OK)
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/rules", response_model=RuleResponse, status_code=status.HTTP_201_CREATED)
def create_rule(payload: RuleCreate) -> RuleResponse:
    rule = RuleResponse(
        rule_id=f"rule_{uuid4()}",
        keyword=payload.keyword,
        dm_message=payload.dm_message,
    )
    rules[rule.rule_id] = rule
    return rule


@router.post("/webhook", status_code=status.HTTP_200_OK)
def receive_webhook(payload: WebhookPayload) -> dict[str, str]:
    return {"status": "accepted"}


@router.get("/stats", response_model=StatsResponse, status_code=status.HTTP_200_OK)
def get_stats() -> StatsResponse:
    return StatsResponse()
