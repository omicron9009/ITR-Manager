"""Schemas — Executive management."""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class ExecutiveCreateRequest(BaseModel):
    email: EmailStr
    full_name: str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., min_length=8, max_length=128)


class ExecutiveResponse(BaseModel):
    id: UUID
    email: str
    full_name: str
    account_status: str
    is_active: bool
    assigned_client_count: int = 0
    active_filing_count: int = 0
    created_at: datetime

    model_config = {"from_attributes": True}


class ExecutiveListResponse(BaseModel):
    items: list[ExecutiveResponse]
    total: int


class ExecutiveAssignRequest(BaseModel):
    executive_id: UUID
    client_id: UUID


class ExecutiveAssignmentResponse(BaseModel):
    id: UUID
    executive_id: UUID
    executive_name: str
    client_id: UUID
    client_name: str
    assigned_at: datetime
    is_active: bool

    model_config = {"from_attributes": True}
