"""Pydantic schemas for the two input sources. Money is integer minor units (cents)."""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class Order(BaseModel):
    order_id: str
    created_at: datetime
    currency: str = Field(min_length=3, max_length=3)
    total: int = Field(ge=0)
    refunded_amount: int = Field(default=0, ge=0)
    financial_status: Literal["paid", "refunded", "partially_refunded", "cancelled"]


class Payment(BaseModel):
    """Stripe-style balance transaction."""
    id: str
    order_id: Optional[str] = None  # from charge metadata
    created: datetime
    type: Literal["charge", "refund"]
    amount: int = Field(ge=0)
    fee: int = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)
    status: Literal["succeeded", "failed", "pending"]
