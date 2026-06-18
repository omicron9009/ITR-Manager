"""Model: client_profiles — Extended client data from onboarding form."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, Date, DateTime, Enum as SAEnum, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.database import Base
from app.enums import ReferralSource


class ClientProfile(Base):
    __tablename__ = "client_profiles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    pan_number = Column(String(10), nullable=True)
    aadhaar_number = Column(String(12), nullable=True)
    date_of_birth = Column(Date, nullable=True)
    contact_number = Column(String(15), nullable=True)
    address = Column(Text, nullable=True)
    income_type = Column(String(50), nullable=True)
    bank_account_details = Column(Text, nullable=True)
    professional_fee = Column(Numeric(10, 2), nullable=True)
    no_fees_applicable = Column(Boolean, nullable=False, server_default="false", default=False)
    referral_source = Column(SAEnum(ReferralSource, name="referral_source"), nullable=True)
    referral_source_other = Column(Text, nullable=True)
    partner_tag_id = Column(UUID(as_uuid=True), ForeignKey("tags.id", ondelete="SET NULL"), nullable=True)
    # WhatsApp opt-in (per-client; phone number is sourced from users.phone_number)
    whatsapp_opt_in = Column(Boolean, nullable=False, server_default="true", default=True)
    form_data = Column(JSONB, nullable=False, default=dict)
    form_submitted_at = Column(DateTime(timezone=True), nullable=True)
    declaration_accepted_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    user = relationship("User", back_populates="client_profile")
