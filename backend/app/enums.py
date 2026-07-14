"""ITR Filing Platform — Enumeration types matching PostgreSQL ENUMs."""

import enum


class UserRole(str, enum.Enum):
    PARTNER = "PARTNER"
    MANAGER = "MANAGER"
    EXECUTIVE = "EXECUTIVE"
    CLIENT = "CLIENT"
    DASHBOARD_USER = "DASHBOARD_USER"


class AccountStatus(str, enum.Enum):
    PENDING_VERIFICATION = "PENDING_VERIFICATION"
    ACTIVE = "ACTIVE"
    REJECTED = "REJECTED"
    DEACTIVATED = "DEACTIVATED"


class FilingStatus(str, enum.Enum):
    INITIATED = "INITIATED"
    DOCUMENT_UPLOAD = "DOCUMENT_UPLOAD"
    PROCESSING = "PROCESSING"
    COMPUTATION = "COMPUTATION"
    FILING = "FILING"
    PAYMENT = "PAYMENT"
    COMPLETED = "COMPLETED"
    HALTED = "HALTED"


class DocumentStatus(str, enum.Enum):
    PENDING_UPLOAD = "PENDING_UPLOAD"
    UPLOADED = "UPLOADED"
    REJECTED = "REJECTED"
    APPROVED = "APPROVED"


class ComputationStatus(str, enum.Enum):
    UPLOADED = "UPLOADED"
    MANAGER_APPROVED = "MANAGER_APPROVED"
    PARTNER_APPROVED = "PARTNER_APPROVED"
    CLIENT_APPROVED = "CLIENT_APPROVED"
    APPROVED = "APPROVED"  # Legacy — alias for CLIENT_APPROVED
    REJECTED = "REJECTED"
    MANAGER_REJECTED = "MANAGER_REJECTED"
    SUPERSEDED = "SUPERSEDED"


class InternalWorkingDocType(str, enum.Enum):
    """Type of internal working document."""
    AIS = "AIS"              # Annual Information Statement
    TIS = "TIS"              # Taxpayer Information Summary
    TWENTY_SIX_AS = "26AS"   # Form 26AS
    OTHER = "OTHER"          # Any other internal document


# Mandatory internal working doc types that must be uploaded before client can confirm tax payment
MANDATORY_INTERNAL_WORKING_TYPES = {
    InternalWorkingDocType.AIS,
    InternalWorkingDocType.TIS,
    InternalWorkingDocType.TWENTY_SIX_AS,
}


class CompletedDocType(str, enum.Enum):
    ITR_ACKNOWLEDGEMENT = "ITR_ACKNOWLEDGEMENT"
    INVOICE = "INVOICE"
    ITR_JSON = "ITR_JSON"
    ITR_FORM = "ITR_FORM"
    TAX_PAID_COMPUTATION = "TAX_PAID_COMPUTATION"
    FINANCIAL_STATEMENT = "FINANCIAL_STATEMENT"


class CompletedDocStatus(str, enum.Enum):
    UPLOADED = "UPLOADED"
    MANAGER_APPROVED = "MANAGER_APPROVED"
    PARTNER_APPROVED = "PARTNER_APPROVED"
    MANAGER_REJECTED = "MANAGER_REJECTED"


class FormFieldType(str, enum.Enum):
    TEXT = "TEXT"
    NUMBER = "NUMBER"
    DATE = "DATE"
    DROPDOWN = "DROPDOWN"
    FILE = "FILE"


class AuditEventType(str, enum.Enum):
    ACCOUNT_REGISTERED = "ACCOUNT_REGISTERED"
    ACCOUNT_ACTIVATED = "ACCOUNT_ACTIVATED"
    ACCOUNT_REJECTED = "ACCOUNT_REJECTED"
    ACCOUNT_DEACTIVATED = "ACCOUNT_DEACTIVATED"
    ACCOUNT_REACTIVATED = "ACCOUNT_REACTIVATED"
    EXECUTIVE_CREATED = "EXECUTIVE_CREATED"
    EXECUTIVE_ASSIGNED = "EXECUTIVE_ASSIGNED"
    EXECUTIVE_UNASSIGNED = "EXECUTIVE_UNASSIGNED"
    FILING_INITIATED = "FILING_INITIATED"
    FILING_STATE_CHANGED = "FILING_STATE_CHANGED"
    FILING_HALTED = "FILING_HALTED"
    DOCUMENT_PLACEHOLDER_CREATED = "DOCUMENT_PLACEHOLDER_CREATED"
    DOCUMENT_PLACEHOLDER_REMOVED = "DOCUMENT_PLACEHOLDER_REMOVED"
    DOCUMENT_UPLOADED = "DOCUMENT_UPLOADED"
    DOCUMENT_APPROVED = "DOCUMENT_APPROVED"
    DOCUMENT_REJECTED = "DOCUMENT_REJECTED"
    DOCUMENT_DOWNLOADED = "DOCUMENT_DOWNLOADED"
    COMPUTATION_UPLOADED = "COMPUTATION_UPLOADED"
    COMPUTATION_APPROVED = "COMPUTATION_APPROVED"
    COMPUTATION_MANAGER_APPROVED = "COMPUTATION_MANAGER_APPROVED"
    COMPUTATION_PARTNER_APPROVED = "COMPUTATION_PARTNER_APPROVED"
    COMPUTATION_MANAGER_REJECTED = "COMPUTATION_MANAGER_REJECTED"
    COMPUTATION_REJECTED = "COMPUTATION_REJECTED"
    COMPUTATION_SUPERSEDED = "COMPUTATION_SUPERSEDED"
    COMPUTATION_REPLACED = "COMPUTATION_REPLACED"
    MANAGER_CREATED = "MANAGER_CREATED"
    MANAGER_EXECUTIVE_ASSIGNED = "MANAGER_EXECUTIVE_ASSIGNED"
    MANAGER_EXECUTIVE_UNASSIGNED = "MANAGER_EXECUTIVE_UNASSIGNED"
    MANAGER_CLIENT_ASSIGNED = "MANAGER_CLIENT_ASSIGNED"
    MANAGER_CLIENT_UNASSIGNED = "MANAGER_CLIENT_UNASSIGNED"
    ITR_FILED = "ITR_FILED"
    PAYMENT_RECEIVED = "PAYMENT_RECEIVED"
    INVOICE_UPLOADED = "INVOICE_UPLOADED"
    FORM_FIELD_ADDED = "FORM_FIELD_ADDED"
    FORM_FIELD_UPDATED = "FORM_FIELD_UPDATED"
    FORM_FIELD_REMOVED = "FORM_FIELD_REMOVED"
    MASTER_DOC_TYPE_ADDED = "MASTER_DOC_TYPE_ADDED"
    MASTER_DOC_TYPE_UPDATED = "MASTER_DOC_TYPE_UPDATED"
    MASTER_DOC_TYPE_REMOVED = "MASTER_DOC_TYPE_REMOVED"
    INCOME_HEADS_CONFIRMED = "INCOME_HEADS_CONFIRMED"
    # Text-field placeholders
    TEXT_FIELD_TYPE_ADDED = "TEXT_FIELD_TYPE_ADDED"
    TEXT_FIELD_TYPE_UPDATED = "TEXT_FIELD_TYPE_UPDATED"
    TEXT_FIELD_TYPE_REMOVED = "TEXT_FIELD_TYPE_REMOVED"
    TEXT_FIELD_PLACEHOLDER_CREATED = "TEXT_FIELD_PLACEHOLDER_CREATED"
    TEXT_FIELD_PLACEHOLDER_REMOVED = "TEXT_FIELD_PLACEHOLDER_REMOVED"
    TEXT_FIELD_FILLED = "TEXT_FIELD_FILLED"
    TEXT_FIELD_APPROVED = "TEXT_FIELD_APPROVED"
    TEXT_FIELD_REJECTED = "TEXT_FIELD_REJECTED"
    # WhatsApp gateway
    WHATSAPP_CONFIGURED = "WHATSAPP_CONFIGURED"
    WHATSAPP_DISCONNECTED = "WHATSAPP_DISCONNECTED"
    WHATSAPP_SESSION_STARTED = "WHATSAPP_SESSION_STARTED"
    WHATSAPP_SESSION_STOPPED = "WHATSAPP_SESSION_STOPPED"
    WHATSAPP_TEST_SENT = "WHATSAPP_TEST_SENT"
    WHATSAPP_CLIENT_OPT_IN_CHANGED = "WHATSAPP_CLIENT_OPT_IN_CHANGED"
    WHATSAPP_SESSION_AUTO_RECONNECTED = "WHATSAPP_SESSION_AUTO_RECONNECTED"
    WHATSAPP_SESSION_LOST = "WHATSAPP_SESSION_LOST"
    MANAGER_ELEVATED = "MANAGER_ELEVATED"
    MANAGER_DE_ELEVATED = "MANAGER_DE_ELEVATED"
    PASSWORD_RESET_REQUESTED = "PASSWORD_RESET_REQUESTED"
    PASSWORD_RESET_VIA_LINK = "PASSWORD_RESET_VIA_LINK"
    # Reminders subsystem
    REMINDER_CONFIG_UPDATED = "REMINDER_CONFIG_UPDATED"
    REMINDER_CONFIG_PAUSED = "REMINDER_CONFIG_PAUSED"
    REMINDER_CONFIG_RESUMED = "REMINDER_CONFIG_RESUMED"
    REMINDER_SENT = "REMINDER_SENT"


class NotificationChannel(str, enum.Enum):
    IN_APP = "IN_APP"
    EMAIL = "EMAIL"
    BOTH = "BOTH"


class TagType(str, enum.Enum):
    LOCATION = "LOCATION"
    PARTNER = "PARTNER"


class ReferralSource(str, enum.Enum):
    WEBSITE = "WEBSITE"
    FRIEND_RELATIVE = "FRIEND_RELATIVE"
    PROFESSIONAL_REFERRAL = "PROFESSIONAL_REFERRAL"
    DIRECTED_BY_FIRM = "DIRECTED_BY_FIRM"
    OTHER = "OTHER"


class IncomeHeadCategory(str, enum.Enum):
    """Categories used to tag MasterDocumentType to client income heads.

    The first 10 values mirror the boolean flags on `client_income_heads`.
    `OTHERS` is a doc-side catch-all bucket — clients never select it.
    """
    SALARY = "SALARY"
    ESOP = "ESOP"
    RENTAL_INCOME = "RENTAL_INCOME"
    MORE_THAN_2_PROPERTIES = "MORE_THAN_2_PROPERTIES"
    CAPITAL_GAIN_SHARES = "CAPITAL_GAIN_SHARES"
    CAPITAL_GAIN_LAND = "CAPITAL_GAIN_LAND"
    BUSINESS_PROFESSION = "BUSINESS_PROFESSION"
    INTEREST_DIVIDEND = "INTEREST_DIVIDEND"
    FOREIGN_ASSETS = "FOREIGN_ASSETS"
    ANY_OTHER = "ANY_OTHER"
    OTHERS = "OTHERS"


# Mapping from IncomeHeadCategory → ClientIncomeHeads boolean field name.
# OTHERS is intentionally absent — it's a doc-side bucket only.
INCOME_HEAD_FLAG_FIELDS: dict[IncomeHeadCategory, str] = {
    IncomeHeadCategory.SALARY: "salary",
    IncomeHeadCategory.ESOP: "esop",
    IncomeHeadCategory.RENTAL_INCOME: "rental_income",
    IncomeHeadCategory.MORE_THAN_2_PROPERTIES: "more_than_2_properties",
    IncomeHeadCategory.CAPITAL_GAIN_SHARES: "capital_gain_shares",
    IncomeHeadCategory.CAPITAL_GAIN_LAND: "capital_gain_land",
    IncomeHeadCategory.BUSINESS_PROFESSION: "business_profession",
    IncomeHeadCategory.INTEREST_DIVIDEND: "interest_dividend",
    IncomeHeadCategory.FOREIGN_ASSETS: "foreign_assets",
    IncomeHeadCategory.ANY_OTHER: "any_other",
}

# Human-friendly labels used by the catalog endpoint and frontend.
INCOME_HEAD_LABELS: dict[IncomeHeadCategory, str] = {
    IncomeHeadCategory.SALARY: "Salary",
    IncomeHeadCategory.ESOP: "ESOP",
    IncomeHeadCategory.RENTAL_INCOME: "Rental Income",
    IncomeHeadCategory.MORE_THAN_2_PROPERTIES: "More Than 2 Properties",
    IncomeHeadCategory.CAPITAL_GAIN_SHARES: "Capital Gain — Shares",
    IncomeHeadCategory.CAPITAL_GAIN_LAND: "Capital Gain — Land",
    IncomeHeadCategory.BUSINESS_PROFESSION: "Business / Profession",
    IncomeHeadCategory.INTEREST_DIVIDEND: "Interest & Dividend",
    IncomeHeadCategory.FOREIGN_ASSETS: "Foreign Assets",
    IncomeHeadCategory.ANY_OTHER: "Any Other",
    IncomeHeadCategory.OTHERS: "Others",
}


class DocSubCategory(str, enum.Enum):
    """Sub-category of a MasterDocumentType under a given income head."""
    BASE = "BASE"
    INCREMENTAL = "INCREMENTAL"


class TextFieldStatus(str, enum.Enum):
    """Lifecycle status for a per-filing text-field placeholder."""
    PENDING = "PENDING"
    FILLED = "FILLED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ActionItemType(str, enum.Enum):
    # Partner / Executive items
    VERIFY_CLIENT = "VERIFY_CLIENT"
    ASSIGN_EXECUTIVE = "ASSIGN_EXECUTIVE"
    SEND_DOCUMENT_CHECKLIST = "SEND_DOCUMENT_CHECKLIST"
    REVIEW_DOCUMENTS = "REVIEW_DOCUMENTS"
    MOVE_TO_COMPUTATION = "MOVE_TO_COMPUTATION"
    UPLOAD_COMPUTATION = "UPLOAD_COMPUTATION"
    REVISE_COMPUTATION = "REVISE_COMPUTATION"
    MOVE_TO_FILING = "MOVE_TO_FILING"
    UPLOAD_COMPLETED_DOCS = "UPLOAD_COMPLETED_DOCS"
    MANAGER_APPROVE_COMPLETED_DOCS = "MANAGER_APPROVE_COMPLETED_DOCS"
    PARTNER_APPROVE_COMPLETED_DOCS = "PARTNER_APPROVE_COMPLETED_DOCS"
    REVISE_COMPLETED_DOC = "REVISE_COMPLETED_DOC"
    MARK_PAYMENT_RECEIVED = "MARK_PAYMENT_RECEIVED"
    SET_PROFESSIONAL_FEE = "SET_PROFESSIONAL_FEE"
    UPLOAD_INVOICE = "UPLOAD_INVOICE"
    # Manager items
    MANAGER_APPROVE_COMPUTATION = "MANAGER_APPROVE_COMPUTATION"
    PARTNER_APPROVE_COMPUTATION = "PARTNER_APPROVE_COMPUTATION"
    ASSIGN_CLIENT_TO_EXECUTIVE = "ASSIGN_CLIENT_TO_EXECUTIVE"
    # Client items
    COMPLETE_ONBOARDING = "COMPLETE_ONBOARDING"
    UPLOAD_DOCUMENTS = "UPLOAD_DOCUMENTS"
    SUBMIT_DOCUMENTS = "SUBMIT_DOCUMENTS"
    REVIEW_COMPUTATION = "REVIEW_COMPUTATION"
    CONFIRM_TAX_PAID = "CONFIRM_TAX_PAID"


class ActionItemPriority(str, enum.Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


# Valid state transitions for the filing state machine
VALID_FILING_TRANSITIONS: dict[FilingStatus, list[FilingStatus]] = {
    FilingStatus.INITIATED: [FilingStatus.DOCUMENT_UPLOAD, FilingStatus.HALTED],
    FilingStatus.DOCUMENT_UPLOAD: [FilingStatus.PROCESSING, FilingStatus.HALTED],
    FilingStatus.PROCESSING: [FilingStatus.DOCUMENT_UPLOAD, FilingStatus.COMPUTATION, FilingStatus.HALTED],
    FilingStatus.COMPUTATION: [FilingStatus.PROCESSING, FilingStatus.FILING, FilingStatus.HALTED],
    FilingStatus.FILING: [FilingStatus.PAYMENT, FilingStatus.HALTED],
    FilingStatus.PAYMENT: [FilingStatus.COMPLETED, FilingStatus.HALTED],
    FilingStatus.HALTED: [
        FilingStatus.INITIATED,
        FilingStatus.DOCUMENT_UPLOAD,
        FilingStatus.PROCESSING,
        FilingStatus.COMPUTATION,
        FilingStatus.FILING,
        FilingStatus.PAYMENT,
    ],
}


# ─── Reminders subsystem ──────────────────────────────────────
class ReminderType(str, enum.Enum):
    """Type of reminder — one entry per configurable rule.

    New reminder types are appended here as new prompts implement them.
    The PG enum `reminder_type` is auto-synced by `_sync_pg_enums`.
    """
    UNASSIGNED_CLIENT = "UNASSIGNED_CLIENT"
    FILING_NOT_INITIATED = "FILING_NOT_INITIATED"
    TAX_PAYMENT_PENDING = "TAX_PAYMENT_PENDING"
    FILING_STAGNANT_PRE_FILING = "FILING_STAGNANT_PRE_FILING"
    INVOICE_PENDING_POST_FILING = "INVOICE_PENDING_POST_FILING"
    CLIENT_DOCS_PENDING_UPLOAD = "CLIENT_DOCS_PENDING_UPLOAD"
    TEXT_FIELDS_PENDING_FILL = "TEXT_FIELDS_PENDING_FILL"
    COMPUTATION_AWAITING_MANAGER_APPROVAL = "COMPUTATION_AWAITING_MANAGER_APPROVAL"
    COMPUTATION_AWAITING_PARTNER_APPROVAL = "COMPUTATION_AWAITING_PARTNER_APPROVAL"
    COMPUTATION_AWAITING_CLIENT_APPROVAL = "COMPUTATION_AWAITING_CLIENT_APPROVAL"
    COMPLETED_DOCS_PENDING = "COMPLETED_DOCS_PENDING"
    PAYMENT_NOT_MARKED_RECEIVED = "PAYMENT_NOT_MARKED_RECEIVED"
    FEEDBACK_NOT_SUBMITTED = "FEEDBACK_NOT_SUBMITTED"


# Human-friendly default titles used when a config has no custom_title override.
REMINDER_DEFAULT_LABELS: dict[ReminderType, str] = {
    ReminderType.UNASSIGNED_CLIENT: "Client not fully assigned",
    ReminderType.FILING_NOT_INITIATED: "Filing not yet initiated",
    ReminderType.TAX_PAYMENT_PENDING: "Tax payment confirmation pending",
    ReminderType.FILING_STAGNANT_PRE_FILING: "Filing not progressing",
    ReminderType.INVOICE_PENDING_POST_FILING: "Invoice upload pending",
    ReminderType.CLIENT_DOCS_PENDING_UPLOAD: "Documents pending upload",
    ReminderType.TEXT_FIELDS_PENDING_FILL: "Information fields pending",
    ReminderType.COMPUTATION_AWAITING_MANAGER_APPROVAL: "Computation awaiting Manager approval",
    ReminderType.COMPUTATION_AWAITING_PARTNER_APPROVAL: "Computation awaiting Partner approval",
    ReminderType.COMPUTATION_AWAITING_CLIENT_APPROVAL: "Computation awaiting your approval",
    ReminderType.COMPLETED_DOCS_PENDING: "Completed documents pending",
    ReminderType.PAYMENT_NOT_MARKED_RECEIVED: "Payment not marked received",
    ReminderType.FEEDBACK_NOT_SUBMITTED: "We'd love your feedback",
}


# Default message templates. Support Python `.format()` placeholders like
# `{client_name}`, `{fy}`, `{days}`, `{missing}`, `{filing_status}`.
REMINDER_DEFAULT_MESSAGES: dict[ReminderType, str] = {
    ReminderType.UNASSIGNED_CLIENT: (
        "Client {client_name} was activated {days} day(s) ago but is missing: {missing}. "
        "Please complete the assignment so filing work can begin."
    ),
    ReminderType.FILING_NOT_INITIATED: (
        "Hi {client_name}, your account has been active for {days} day(s) but you haven't "
        "initiated your ITR filing for FY {fy} yet. Please start the filing to receive your "
        "document checklist."
    ),
    ReminderType.TAX_PAYMENT_PENDING: (
        "Hi {client_name}, your ITR computation for FY {fy} was approved {days} day(s) ago but "
        "we haven't received your tax payment confirmation yet. Please pay the tax and confirm, "
        "or let us know if a refund is expected."
    ),
    ReminderType.FILING_STAGNANT_PRE_FILING: (
        "Filing for {client_name} (FY {fy}) is in {filing_status} with all documents approved but "
        "hasn't progressed for {days} day(s). Please advance it toward FILING."
    ),
    ReminderType.INVOICE_PENDING_POST_FILING: (
        "Filing for {client_name} (FY {fy}) entered FILING {days} day(s) ago but the invoice "
        "hasn't been uploaded and Partner-approved yet."
    ),
    ReminderType.CLIENT_DOCS_PENDING_UPLOAD: (
        "Hi {client_name}, your filing for FY {fy} is waiting on document uploads for "
        "{days} day(s). Please complete the checklist to move forward."
    ),
    ReminderType.TEXT_FIELDS_PENDING_FILL: (
        "Hi {client_name}, we need some information from you for your FY {fy} filing. "
        "There are pending fields waiting for {days} day(s)."
    ),
    ReminderType.COMPUTATION_AWAITING_MANAGER_APPROVAL: (
        "Computation for {client_name} (FY {fy}) was uploaded {days} day(s) ago and is waiting "
        "for your review."
    ),
    ReminderType.COMPUTATION_AWAITING_PARTNER_APPROVAL: (
        "Computation for {client_name} (FY {fy}) was Manager-approved {days} day(s) ago and is "
        "awaiting Partner sign-off."
    ),
    ReminderType.COMPUTATION_AWAITING_CLIENT_APPROVAL: (
        "Hi {client_name}, your ITR computation for FY {fy} was Partner-approved {days} day(s) "
        "ago and is waiting for your review."
    ),
    ReminderType.COMPLETED_DOCS_PENDING: (
        "Filing for {client_name} (FY {fy}) has been in FILING for {days} day(s). "
        "Pending completed docs: {missing_docs}."
    ),
    ReminderType.PAYMENT_NOT_MARKED_RECEIVED: (
        "Filing for {client_name} (FY {fy}) has been in PAYMENT for {days} day(s) but the "
        "payment received flag is still not set."
    ),
    ReminderType.FEEDBACK_NOT_SUBMITTED: (
        "Hi {client_name}, your FY {fy} filing was completed {days} day(s) ago. "
        "We'd appreciate a quick rating so we can serve you better next year."
    ),
}
