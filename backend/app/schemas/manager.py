"""Schemas — Manager management."""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class ManagerCreateRequest(BaseModel):
    email: EmailStr
    full_name: str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., min_length=8, max_length=128)


class ManagerResponse(BaseModel):
    id: UUID
    email: str
    full_name: str
    account_status: str
    is_active: bool
    is_elevated: bool = False
    team_executive_count: int = 0
    team_client_count: int = 0
    created_at: datetime

    model_config = {"from_attributes": True}


class ManagerListResponse(BaseModel):
    items: list[ManagerResponse]
    total: int


class ManagerAssignExecutiveRequest(BaseModel):
    executive_id: UUID


class ManagerAssignClientRequest(BaseModel):
    client_id: UUID


class ManagerAssignTagRequest(BaseModel):
    tag_id: UUID


class ManagerExecutiveAssignmentResponse(BaseModel):
    id: UUID
    manager_id: UUID
    manager_name: str
    executive_id: UUID
    executive_name: str
    assigned_at: datetime
    is_active: bool

    model_config = {"from_attributes": True}


class ManagerClientAssignmentResponse(BaseModel):
    id: UUID
    manager_id: UUID
    manager_name: str
    client_id: UUID
    client_name: str
    assigned_at: datetime
    is_active: bool

    model_config = {"from_attributes": True}


class ManagerClientListItem(BaseModel):
    id: UUID
    full_name: str
    email: str
    phone_number: Optional[str] = None
    account_status: str
    assignment_status: str  # PENDING_EXECUTIVE or ASSIGNED
    assigned_executive_name: Optional[str] = None
    assigned_executive_id: Optional[UUID] = None
    active_filing_year: Optional[str] = None
    current_filing_state: Optional[str] = None

    model_config = {"from_attributes": True}


class ManagerClientListResponse(BaseModel):
    items: list[ManagerClientListItem]
    total: int
    page: int
    page_size: int


class ManagerTeamResponse(BaseModel):
    manager_id: UUID
    manager_name: str
    executives: list["ManagerTeamExecutiveItem"]


class ManagerTeamExecutiveItem(BaseModel):
    id: UUID
    email: str
    full_name: str
    account_status: str
    is_active: bool
    assigned_client_count: int = 0
    active_filing_count: int = 0

    model_config = {"from_attributes": True}
