from __future__ import annotations

import logging

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, status
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import COOKIE_NAME, get_db, get_optional_user
from app.core.config import settings
from app.schemas.feedback import FeedbackRequest, FeedbackResponse
from app.services.feedback import feedback_email_configured, send_feedback_email

router = APIRouter(tags=["feedback"])
logger = logging.getLogger(__name__)


@router.post("/feedback", response_model=FeedbackResponse)
async def submit_feedback(
    request: Request,
    db: AsyncSession = Depends(get_db),
    access_token: str | None = Cookie(default=None, alias=COOKIE_NAME),
) -> FeedbackResponse:
    try:
        raw_payload = await request.json()
        payload = FeedbackRequest.model_validate(raw_payload)
    except (ValueError, ValidationError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid feedback submission",
        ) from None

    user = await get_optional_user(db, access_token)
    enabled = (
        settings.feedback_authenticated_enabled_value()
        if user is not None
        else settings.feedback_public_enabled_value()
    )
    if not enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Feedback is currently unavailable",
        )

    if payload.website:
        return FeedbackResponse()

    if not feedback_email_configured():
        logger.warning("Feedback delivery unavailable because configuration is incomplete")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Feedback is currently unavailable",
        )

    try:
        await send_feedback_email(db, payload=payload, user=user)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid feedback submission",
        ) from None
    except RuntimeError:
        logger.warning(
            "Feedback delivery failed type=%s authenticated=%s",
            payload.type,
            user is not None,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Feedback is currently unavailable",
        ) from None

    logger.info(
        "Feedback delivered type=%s authenticated=%s diagnostics=%s",
        payload.type,
        user is not None,
        payload.include_diagnostics,
    )
    return FeedbackResponse()
