"""Model: client_income_heads — Income head flags collected during client registration."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


class ClientIncomeHeads(Base):
    __tablename__ = "client_income_heads"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)

    # Head 1: Salary
    salary = Column(Boolean, nullable=False, default=False)
    esop = Column(Boolean, nullable=False, default=False)

    # Head 2: House Property
    rental_income = Column(Boolean, nullable=False, default=False)
    more_than_2_properties = Column(Boolean, nullable=False, default=False)

    # Head 3: Capital Gain
    capital_gain_shares = Column(Boolean, nullable=False, default=False)
    capital_gain_land = Column(Boolean, nullable=False, default=False)

    # Head 4: Business/Profession
    business_profession = Column(Boolean, nullable=False, default=False)

    # Head 5: Other Sources
    interest_dividend = Column(Boolean, nullable=False, default=False)
    foreign_assets = Column(Boolean, nullable=False, default=False)

    # Head 6: Any Other
    any_other = Column(Boolean, nullable=False, default=False)

    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    # Relationships
    user = relationship("User", back_populates="income_heads")
