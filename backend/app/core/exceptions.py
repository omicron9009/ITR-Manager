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
        super().__init__(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid state transition from {from_status} to {to_status}",
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
            detail="An Executive must be assigned before proceeding",
        )
