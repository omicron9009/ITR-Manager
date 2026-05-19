"""Core — Custom exception classes."""

from fastapi import HTTPException, status


class FilingNotFoundError(HTTPException):
    def __init__(self):
        super().__init__(status_code=status.HTTP_404_NOT_FOUND, detail="Filing not found")


class ClientNotFoundError(HTTPException):
    def __init__(self):
        super().__init__(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")


class DuplicateFilingError(HTTPException):
    def __init__(self, financial_year: str):
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A filing already exists for financial year {financial_year}",
        )


class InvalidStateTransitionError(HTTPException):
    def __init__(self, from_status: str, to_status: str):
        valid_map = {
            "INITIATED": "DOCUMENT_UPLOAD (assign document placeholders)",
            "DOCUMENT_UPLOAD": "PROCESSING (client submits documents)",
            "PROCESSING": "COMPUTATION (all documents approved) or DOCUMENT_UPLOAD",
            "COMPUTATION": "FILING (client approves computation) or PROCESSING (request more docs)",
            "FILING": "PAYMENT (Executive marks filed + uploads docs)",
            "PAYMENT": "COMPLETED (payment received)",
        }
        hint = valid_map.get(from_status, "")
        detail = f"Invalid state transition from {from_status} to {to_status}."
        if hint:
            detail += f" From {from_status}, valid next states are: {hint}."
        super().__init__(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=detail,
        )


class DocumentNotFoundError(HTTPException):
    def __init__(self):
        super().__init__(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")


class AccountNotActiveError(HTTPException):
    def __init__(self):
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is not active. Filing actions are disabled.",
        )


class ExecutiveNotAssignedError(HTTPException):
    def __init__(self):
        super().__init__(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Cannot transition to DOCUMENT_UPLOAD: No Executive is assigned to this client. "
                   "The Partner must assign an Executive before document placeholders can be set.",
        )


class ComputationNotUploadedError(HTTPException):
    def __init__(self):
        super().__init__(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No computation document has been uploaded. The Executive/Partner must upload a computation before the client can approve.",
        )


class DocumentsNotAllApprovedError(HTTPException):
    def __init__(self, pending: int, rejected: int):
        super().__init__(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Not all documents are approved. {pending} pending upload, {rejected} rejected. All documents must be approved before computation.",
        )


class FilingDocumentsNotUploadedError(HTTPException):
    def __init__(self, missing_types: list[str]):
        super().__init__(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"The following required documents must be uploaded before proceeding: {', '.join(missing_types)}",
        )


class OnboardingFormNotSubmittedError(HTTPException):
    def __init__(self):
        super().__init__(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="You must fill and submit the onboarding form before initiating a filing. Please complete your profile first.",
        )
