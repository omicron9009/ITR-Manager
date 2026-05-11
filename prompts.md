# ITR Filing Platform — Implementation Prompts

> Each prompt below is self-contained and designed to execute **3 independent tasks in one shot**.
> Every prompt includes the exact files to modify, the current code context, and the precise logic to implement.
> Execute prompts **in order** (Prompt 1 → 5) since later prompts may depend on enum/model additions from earlier ones.

---

## Prompt 1 — Onboarding Form: Filing Gate + Delete/Re-create Fix + Sanity Checks

### Context & References

**Files to modify:**
- `backend/app/api/v1/filings.py` — `initiate_filing()` endpoint
- `backend/app/api/v1/onboarding.py` — `deactivate_form_field()`, `create_form_field()`, `submit_onboarding_form()`
- `backend/app/models/onboarding_form_field.py` — `OnboardingFormField` model
- `backend/app/core/exceptions.py` — add new exception

**Current state:**
- `initiate_filing()` in `filings.py` (line ~47) only checks `check_duplicate_filing()` and requires `get_current_active_client`. It does NOT check whether the client has submitted the onboarding form.
- `ClientProfile.form_submitted_at` (in `models/client_profile.py`) is `NULL` until the form is submitted — this is the gate check.
- `OnboardingFormField.field_key` has a `unique=True` constraint. When a field is soft-deleted (`is_active=False`), the `field_key` row still exists. Re-creating a field with the same `field_key` fails with a duplicate key DB error.
- `submit_onboarding_form()` in `onboarding.py` does NOT validate that all `is_required=True` fields are present in the submitted `form_data`.

### Task 1 — Block filing initiation if onboarding form not submitted

**What to do:**

In `backend/app/api/v1/filings.py`, inside the `initiate_filing()` function, after the `check_duplicate_filing()` call and before creating the `ITRFiling` record, add a check:

```python
# Check onboarding form submission
from app.models.client_profile import ClientProfile

profile_result = await db.execute(
    select(ClientProfile).where(ClientProfile.user_id == current_user.id)
)
profile = profile_result.scalar_one_or_none()

if not profile or not profile.form_submitted_at:
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail="You must fill and submit the onboarding form before initiating a filing. Please complete your profile first.",
    )
```

This ensures BRD §7 STATE 1: "Client fills onboarding form (first time) or confirms pre-filled profile → submits" before a filing can be created.

### Task 2 — Fix delete/re-create field_key uniqueness issue

**What to do:**

The problem: `OnboardingFormField.field_key` is `unique=True` at the DB level. Soft-deleting (`is_active=False`) does not free the key. Re-creating with the same key fails.

**Option chosen:** On `deactivate_form_field()`, actually delete the row from DB if no client profiles reference it, OR change the unique constraint approach.

Best approach: In `backend/app/api/v1/onboarding.py`, modify `deactivate_form_field()`:

1. When deleting a field, check if any `ClientProfile.form_data` references the `field_key`. If NOT referenced, do a **hard delete** (`await db.delete(field)`). If referenced, do a soft delete but **rename the field_key** to `{field_key}__deleted_{uuid[:8]}` to free up the key name.

```python
@router.delete("/fields/{field_id}", response_model=dict)
async def deactivate_form_field(
    field_id: UUID,
    current_user: User = Depends(get_current_partner),
    db: AsyncSession = Depends(get_db),
):
    """Delete a form field (Partner only). Hard-deletes if unused, soft-deletes if referenced."""
    import uuid as uuid_mod
    from sqlalchemy import func as sa_func

    result = await db.execute(select(OnboardingFormField).where(OnboardingFormField.id == field_id))
    field = result.scalar_one_or_none()
    if not field:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Form field not found")

    field_key = field.field_key
    field_label = field.field_label

    # Check if any client profile references this field_key in form_data
    # PostgreSQL JSONB: check if key exists in form_data
    from app.models.client_profile import ClientProfile
    ref_result = await db.execute(
        select(sa_func.count()).select_from(ClientProfile).where(
            ClientProfile.form_data.has_key(field_key)
        )
    )
    ref_count = ref_result.scalar() or 0

    if ref_count == 0:
        # No references — hard delete to free up field_key
        await db.delete(field)
    else:
        # Soft delete: mark inactive + rename field_key to avoid unique constraint collision
        field.is_active = False
        field.field_key = f"{field_key}__deleted_{str(uuid_mod.uuid4())[:8]}"
        field.updated_by = current_user.id

    await record_audit_event(
        db=db,
        event_type=AuditEventType.FORM_FIELD_REMOVED,
        actor_id=current_user.id,
        details={"field_id": str(field_id), "field_key": field_key},
    )

    await db.flush()
    return {"message": f"Form field '{field_label}' deleted"}
```

Also, in `create_form_field()`, add a check for existing inactive field with the same key:

```python
# Before creating, check if an inactive field with the same key exists
existing_result = await db.execute(
    select(OnboardingFormField).where(OnboardingFormField.field_key == body.field_key)
)
existing = existing_result.scalar_one_or_none()
if existing:
    if existing.is_active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A form field with key '{body.field_key}' already exists",
        )
    else:
        # Reactivate and update the existing field instead of creating new
        existing.field_label = body.field_label
        existing.field_key = body.field_key
        existing.field_type = body.field_type
        existing.field_options = body.field_options
        existing.is_required = body.is_required
        existing.display_order = body.display_order
        existing.is_active = True
        existing.updated_by = current_user.id
        await db.flush()
        return FormFieldResponse.model_validate(existing)
```

### Task 3 — Onboarding form submission sanity checks (required fields validation)

**What to do:**

In `backend/app/api/v1/onboarding.py`, inside `submit_onboarding_form()`, before saving `form_data` to the profile, validate that all `is_required=True` active fields have values:

```python
# Validate required fields
required_fields_result = await db.execute(
    select(OnboardingFormField).where(
        OnboardingFormField.is_active == True,
        OnboardingFormField.is_required == True,
    )
)
required_fields = required_fields_result.scalars().all()

missing_fields = []
for field in required_fields:
    value = body.form_data.get(field.field_key)
    if value is None or (isinstance(value, str) and value.strip() == ""):
        missing_fields.append(field.field_label)

if missing_fields:
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=f"The following required fields are missing or empty: {', '.join(missing_fields)}",
    )

# Validate dropdown values are in allowed options
dropdown_fields_result = await db.execute(
    select(OnboardingFormField).where(
        OnboardingFormField.is_active == True,
        OnboardingFormField.field_type == FormFieldType.DROPDOWN,
    )
)
dropdown_fields = dropdown_fields_result.scalars().all()

for field in dropdown_fields:
    value = body.form_data.get(field.field_key)
    if value is not None and field.field_options and value not in field.field_options:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid value '{value}' for field '{field.field_label}'. Allowed: {', '.join(field.field_options)}",
        )
```

---

## Prompt 2 — Client Activation Notification + MinIO Name_UUID + Executive Assignment Check

### Context & References

**Files to modify:**
- `backend/app/services/client_service.py` — `activate_client()` function
- `backend/app/services/storage_service.py` — `generate_object_key()`, `generate_pan_object_key()`
- `backend/app/api/v1/documents.py` — `assign_documents_to_filing()` endpoint
- `backend/app/api/v1/filings.py` — `transition_filing()` endpoint
- `backend/app/services/filing_service.py` — `transition_filing_status()`
- `backend/app/core/exceptions.py` — verify `ExecutiveNotAssignedError` exists

**Current state:**
- `activate_client()` in `client_service.py` sets `account_status=ACTIVE`, creates a notification, and records an audit event. The BRD says the client should also be notified via email (handled separately by email service). The existing code already creates a notification "Your account has been verified." — **verify this exists and is correct**.
- `generate_object_key()` in `storage_service.py` uses pattern `clients/{client_id}/ITR-{FY}/{folder}/{uuid}_{filename}` — `client_id` is a UUID, not human-readable.
- `assign_documents_to_filing()` in `documents.py` does NOT check if an executive is assigned before allowing transition to `ON_BOARDING`. The `transition_filing_status()` in `filing_service.py` does check `if to_status == FilingStatus.ON_BOARDING and not filing.assigned_executive_id` but the error needs better reporting.

### Task 1 — Ensure client activation sets ACTIVE + notification (verify & fix if needed)

**What to do:**

Review `backend/app/services/client_service.py` `activate_client()`:
- It currently sets `client.account_status = AccountStatus.ACTIVE` ✓
- It sets `client.activated_at` and `client.activated_by` ✓
- It creates notification "Your account has been verified. You may now initiate your ITR filing." ✓
- It records audit event `ACCOUNT_ACTIVATED` ✓
- **Missing:** `client.is_active` is NOT set to `True` — it defaults to `True` at creation but is NOT explicitly set on activation.

Add to `activate_client()` after setting `account_status`:
```python
client.is_active = True
```

Also verify that the account was actually in `PENDING_VERIFICATION` state before activating:
```python
if client.account_status != AccountStatus.PENDING_VERIFICATION:
    from app.core.exceptions import InvalidStateTransitionError
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=f"Client account is in '{client.account_status.value}' state, not PENDING_VERIFICATION. Cannot activate.",
    )
```

Add this check right after fetching the client and before changing the status.

### Task 2 — MinIO path: clients/{Name}_{uuid}/ format

**What to do:**

In `backend/app/services/storage_service.py`, change `generate_object_key()` and `generate_pan_object_key()` to accept `client_name` parameter and use `{sanitized_name}_{client_id}` as the directory:

```python
import re

def _sanitize_name_for_path(name: str) -> str:
    """Sanitize a client name for use in file paths."""
    # Replace spaces with underscores, remove special chars, lowercase
    sanitized = re.sub(r'[^a-zA-Z0-9_]', '', name.replace(' ', '_'))
    return sanitized or "client"


def generate_object_key(client_id: str, financial_year: str, folder: str, filename: str, client_name: str = "") -> str:
    """
    Generate a structured object key for MinIO.
    Pattern: clients/{Name}_{client_id}/ITR-{FY}/{folder}/{uuid}_{filename}
    """
    unique_prefix = str(uuid.uuid4())[:8]
    if client_name:
        sanitized = _sanitize_name_for_path(client_name)
        client_dir = f"{sanitized}_{client_id}"
    else:
        client_dir = client_id
    return f"clients/{client_dir}/ITR-{financial_year}/{folder}/{unique_prefix}_{filename}"


def generate_pan_object_key(client_id: str, filename: str, client_name: str = "") -> str:
    """Generate object key for PAN document uploads."""
    unique_prefix = str(uuid.uuid4())[:8]
    if client_name:
        sanitized = _sanitize_name_for_path(client_name)
        client_dir = f"{sanitized}_{client_id}"
    else:
        client_dir = client_id
    return f"clients/{client_dir}/pan/{unique_prefix}_{filename}"
```

Then update **all callers** to pass `client_name`:

1. `backend/app/api/v1/documents.py` → `get_document_upload_url()`: Fetch client user to get `full_name`, pass to `generate_object_key(..., client_name=client_user.full_name)`.
   The client user can be fetched via: 
   ```python
   client_user_result = await db.execute(select(User).where(User.id == filing.client_id))
   client_user = client_user_result.scalar_one_or_none()
   ```
   Then: `generate_object_key(client_id=str(filing.client_id), ..., client_name=client_user.full_name if client_user else "")`

2. `backend/app/api/v1/computations.py` → `get_computation_upload_url()`: Same pattern.

3. `backend/app/api/v1/storage.py` → `get_pan_upload_url()`: Use `current_user.full_name`.
   `get_completed_doc_upload_url()`: Fetch client name from filing.

### Task 3 — Executive assignment check before ON_BOARDING + proper error reporting

**What to do:**

In `backend/app/api/v1/documents.py` → `assign_documents_to_filing()`:

Before the transition `filing.status == FilingStatus.INITIATED → ON_BOARDING`, explicitly check for executive assignment and return a clear error:

```python
# Check executive is assigned before moving to ON_BOARDING
if filing.status == FilingStatus.INITIATED:
    if not filing.assigned_executive_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="An Executive must be assigned to this client before document placeholders can be assigned. "
                   "Please assign an Executive first via the Executive Management page.",
        )
```

Add this check right before the `if filing.status == FilingStatus.INITIATED:` block that calls `transition_filing_status()`.

Also in `backend/app/services/filing_service.py`, enhance the `ExecutiveNotAssignedError` message in `transition_filing_status()` — the error is already raised but the message should be more descriptive. Update `backend/app/core/exceptions.py`:

```python
class ExecutiveNotAssignedError(HTTPException):
    def __init__(self):
        super().__init__(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Cannot transition to ON_BOARDING: No Executive is assigned to this client. "
                   "The Partner must assign an Executive before document placeholders can be set.",
        )
```

---

## Prompt 3 — State Machine: COMPUTATION→PROCESSING Backward + Computation Approval Loop + Error Reporting

### Context & References

**Files to modify:**
- `backend/app/enums.py` — `VALID_FILING_TRANSITIONS` dict
- `backend/app/api/v1/filings.py` — `transition_filing()` endpoint
- `backend/app/api/v1/computations.py` — `approve_computation()` endpoint
- `backend/app/services/filing_service.py` — `transition_filing_status()`
- `backend/app/core/exceptions.py` — add new exceptions

**Current state:**
- `VALID_FILING_TRANSITIONS` in `enums.py`:
  - `COMPUTATION` can go to `[FILING, HALTED]` — does NOT allow going back to `PROCESSING`.
  - `PROCESSING` can go to `[ON_BOARDING, COMPUTATION, HALTED]`.
- `approve_computation()` in `computations.py` transitions filing from `COMPUTATION` → `FILING` when client approves. But it does NOT verify the filing is actually in `COMPUTATION` state before doing so.
- The generic `transition_filing()` endpoint blocks `COMPUTATION → FILING` (must use approve computation), but does NOT block `COMPUTATION → PROCESSING` since it's not even a valid transition currently.

### Task 1 — Allow COMPUTATION → PROCESSING backward transition (request more documents)

**What to do:**

1. In `backend/app/enums.py`, update `VALID_FILING_TRANSITIONS`:
```python
FilingStatus.COMPUTATION: [FilingStatus.PROCESSING, FilingStatus.FILING, FilingStatus.HALTED],
```
Add `FilingStatus.PROCESSING` as a valid target from `COMPUTATION`.

2. In `backend/app/api/v1/filings.py` → `transition_filing()`, add the backward transition to the `allowed_forward` set (rename to `allowed_generic`):
```python
allowed_forward = {
    (FilingStatus.INITIATED, FilingStatus.ON_BOARDING),
    (FilingStatus.COMPUTATION, FilingStatus.PROCESSING),  # Allow requesting more docs
    (FilingStatus.FILING, FilingStatus.PAYMENT),
    (FilingStatus.PAYMENT, FilingStatus.COMPLETED),
}
```

3. When transitioning `COMPUTATION → PROCESSING`, reset relevant timestamps in `filing_service.py` → `transition_filing_status()`:
```python
elif to_status == FilingStatus.PROCESSING and from_status == FilingStatus.COMPUTATION:
    filing.documents_approved_at = None  # Reset since we're going back for more docs
    filing.documents_submitted_at = None
```

This ensures that when an Executive/Partner needs more documents after computation, they can push the state back to PROCESSING, the client can upload more docs, and the cycle continues without breaking the pipeline.

### Task 2 — Computation approval loop check before FILING state

**What to do:**

In `backend/app/api/v1/computations.py` → `approve_computation()`:

1. **Check filing is in COMPUTATION state** before allowing approval:
```python
if filing.status != FilingStatus.COMPUTATION:
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=f"Cannot approve computation: Filing is in '{filing.status.value}' state, not COMPUTATION. "
               f"The computation can only be approved when the filing is in COMPUTATION state.",
    )
```

2. **Check computation is in UPLOADED status** (not already APPROVED or SUPERSEDED):
```python
if computation.status != ComputationStatus.UPLOADED:
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=f"Cannot approve computation: Computation is in '{computation.status.value}' status. "
               f"Only computations with 'UPLOADED' status can be approved.",
    )
```

3. **Verify there is exactly one active (UPLOADED) computation** — not zero, not ambiguous:
```python
active_comps_result = await db.execute(
    select(func.count()).select_from(FilingComputation).where(
        FilingComputation.filing_id == filing.id,
        FilingComputation.status == ComputationStatus.UPLOADED,
    )
)
active_count = active_comps_result.scalar() or 0
if active_count == 0:
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail="No active computation found to approve. The Executive/Partner must upload a computation first.",
    )
```

Add `from sqlalchemy import func` import at the top of `computations.py`.

### Task 3 — Logic-based error reporting (don't change state on error, report properly)

**What to do:**

Ensure all state transitions and actions return descriptive error messages that explain:
- **What** the user tried to do
- **Why** it failed (current state, missing prerequisite)
- **What** they should do instead

In `backend/app/core/exceptions.py`, add:

```python
class OnboardingFormNotSubmittedError(HTTPException):
    def __init__(self):
        super().__init__(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Onboarding form has not been submitted. Please complete and submit the onboarding form before initiating a filing.",
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
```

In `backend/app/services/filing_service.py` → `transition_filing_status()`, enhance error detail in `InvalidStateTransitionError`:

```python
raise InvalidStateTransitionError(from_status.value, to_status.value)
```

Update the exception class:
```python
class InvalidStateTransitionError(HTTPException):
    def __init__(self, from_status: str, to_status: str):
        valid_map = {
            "INITIATED": "ON_BOARDING (assign document placeholders)",
            "ON_BOARDING": "PROCESSING (client submits documents)",
            "PROCESSING": "COMPUTATION (all documents approved) or ON_BOARDING",
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
```

---

## Prompt 4 — Filing Completion: 3 Required Docs (Ack + Invoice + ITR JSON) + Client Visibility After Payment

### Context & References

**Files to modify:**
- `backend/app/enums.py` — `CompletedDocType` enum
- `backend/app/api/v1/storage.py` — `confirm_completed_doc_upload()`, `get_completed_docs()`
- `backend/app/api/v1/filings.py` — `mark_payment_received()` (or create new endpoint)
- `backend/app/api/v1/dashboard.py` — `get_filing_directory()` completed docs visibility
- `backend/app/models/filing_completed_doc.py` — unique constraint update
- `backend/app/schemas/dashboard.py` — may need update for visibility flag

**Current state:**
- `CompletedDocType` enum has only: `ITR_ACKNOWLEDGEMENT`, `INVOICE`.
- `confirm_completed_doc_upload()` in `storage.py` transitions `FILING → PAYMENT` when `ITR_ACKNOWLEDGEMENT` is uploaded. It only requires ONE doc (ack) for the state transition.
- `get_completed_docs()` in `storage.py` returns all docs for Partner/Executive, but for Client only returns docs when `filing.status == FilingStatus.COMPLETED`.
- `FilingCompletedDoc` has a unique constraint `(filing_id, doc_type)`.

### Task 1 — Add ITR_JSON doc type + require all 3 docs before FILING → PAYMENT transition

**What to do:**

1. In `backend/app/enums.py`, add to `CompletedDocType`:
```python
class CompletedDocType(str, enum.Enum):
    ITR_ACKNOWLEDGEMENT = "ITR_ACKNOWLEDGEMENT"
    INVOICE = "INVOICE"
    ITR_JSON = "ITR_JSON"
```

2. In `backend/app/api/v1/storage.py` → `confirm_completed_doc_upload()`:

Add JSON file validation for `ITR_JSON` type:
```python
# Validate ITR_JSON file type
if doc_type == CompletedDocType.ITR_JSON:
    if not filename.lower().endswith('.json'):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="ITR JSON file must have a .json extension.",
        )
    if content_type not in ('application/json', 'text/json'):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="ITR JSON file must have content type 'application/json'.",
        )
```

3. **Change the state transition logic**: Instead of transitioning `FILING → PAYMENT` immediately when ITR_ACKNOWLEDGEMENT is uploaded, check if ALL 3 required docs are present:

Remove the existing auto-transition block and replace with:
```python
# Check if all 3 required completed docs are uploaded — only then transition to PAYMENT
if filing.status == FilingStatus.FILING:
    required_doc_types = {CompletedDocType.ITR_ACKNOWLEDGEMENT, CompletedDocType.INVOICE, CompletedDocType.ITR_JSON}
    existing_docs_result = await db.execute(
        select(FilingCompletedDoc.doc_type).where(FilingCompletedDoc.filing_id == filing_id)
    )
    existing_types = {row[0] for row in existing_docs_result.all()}

    missing_types = required_doc_types - existing_types
    if not missing_types:
        # All 3 docs uploaded → transition to PAYMENT
        await transition_filing_status(
            db=db,
            filing=filing,
            to_status=FilingStatus.PAYMENT,
            changed_by=current_user.id,
            remarks="ITR filed - all required documents uploaded (Acknowledgement, Invoice, ITR JSON)",
        )

        await record_audit_event(
            db=db,
            event_type=AuditEventType.ITR_FILED,
            actor_id=current_user.id,
            client_id=filing.client_id,
            filing_id=filing_id,
        )

        await create_notification(
            db=db,
            user_id=filing.client_id,
            title="ITR Filed Successfully",
            message=f"Your ITR for {filing.financial_year} has been filed. Please complete payment.",
            related_filing_id=filing_id,
        )
    else:
        # Not all docs yet — inform the uploader what's still missing
        missing_names = [t.value for t in missing_types]
        # Don't error — just return info about what's still needed
        # The response will include this info
```

Update the return value at the end to include remaining doc info:
```python
# At the end of confirm_completed_doc_upload, update the response:
if filing.status == FilingStatus.FILING:
    remaining = [t.value for t in (required_doc_types - existing_types)]
    return {
        "message": f"{doc_type.value} uploaded successfully",
        "file_id": str(stored_file.id),
        "remaining_docs": remaining,
        "all_docs_uploaded": len(remaining) == 0,
    }
```

### Task 2 — Client can only see Acknowledgement and Invoice after payment is COMPLETED

**What to do:**

In `backend/app/api/v1/storage.py` → `get_completed_docs()`:

The current code already returns `[]` for clients when `filing.status != FilingStatus.COMPLETED`. This is correct per the BRD (§7 STATE 7: "Invoice and all filed return documents become accessible to the client inside ITR-FY/Filed Documents/ — documents in this folder are locked / not visible to the client until the COMPLETED state is reached").

**Enhancement needed**: Even after COMPLETED, only return `ITR_ACKNOWLEDGEMENT` and `INVOICE` to the client. The `ITR_JSON` is an internal document:

```python
# Clients can only view filed documents after COMPLETED state
if current_user.role == UserRole.CLIENT:
    if filing.status != FilingStatus.COMPLETED:
        return []
    # Only show Acknowledgement and Invoice to client, not ITR JSON (internal)
    result = await db.execute(
        select(FilingCompletedDoc).where(
            FilingCompletedDoc.filing_id == filing_id,
            FilingCompletedDoc.doc_type.in_([CompletedDocType.ITR_ACKNOWLEDGEMENT, CompletedDocType.INVOICE]),
        )
    )
else:
    result = await db.execute(
        select(FilingCompletedDoc).where(FilingCompletedDoc.filing_id == filing_id)
    )
```

Similarly in `backend/app/api/v1/dashboard.py` → `get_filing_directory()`:

Update the completed docs section:
```python
# Completed Docs visibility rules:
# - Client: only visible after COMPLETED, and only Ack + Invoice (not ITR JSON)
# - Partner/Executive: always visible
if current_user.role == UserRole.CLIENT:
    if filing.status == FilingStatus.COMPLETED:
        completed_result = await db.execute(
            select(FilingCompletedDoc).where(
                FilingCompletedDoc.filing_id == filing_id,
                FilingCompletedDoc.doc_type.in_([CompletedDocType.ITR_ACKNOWLEDGEMENT, CompletedDocType.INVOICE]),
            )
        )
        completed_docs = completed_result.scalars().all()
    else:
        completed_docs = []
elif current_user.role in (UserRole.PARTNER, UserRole.EXECUTIVE):
    completed_result = await db.execute(
        select(FilingCompletedDoc).where(FilingCompletedDoc.filing_id == filing_id)
    )
    completed_docs = completed_result.scalars().all()
```

Also update the `mark_payment_received()` endpoint in `filings.py` to verify that all 3 docs exist before allowing PAYMENT → COMPLETED:

```python
# In mark_payment_received(), before transitioning to COMPLETED:
from app.enums import CompletedDocType
from app.models.filing_completed_doc import FilingCompletedDoc

required_types = {CompletedDocType.ITR_ACKNOWLEDGEMENT, CompletedDocType.INVOICE, CompletedDocType.ITR_JSON}
existing_result = await db.execute(
    select(FilingCompletedDoc.doc_type).where(FilingCompletedDoc.filing_id == filing_id)
)
existing_types = {row[0] for row in existing_result.all()}
missing = required_types - existing_types
if missing:
    missing_names = [t.value for t in missing]
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=f"Cannot mark payment as completed. The following documents are still missing: {', '.join(missing_names)}",
    )
```

### Task 3 — Invoice upload state validation fix

Currently, Invoice can only be uploaded in `PAYMENT` or `COMPLETED` state. Per the new flow, Invoice should be uploadable in `FILING` state too (since all 3 docs must be uploaded in FILING state before it transitions to PAYMENT):

In `storage.py` → `confirm_completed_doc_upload()`, update the invoice validation:
```python
if doc_type == CompletedDocType.INVOICE:
    if filing.status not in (FilingStatus.FILING, FilingStatus.PAYMENT, FilingStatus.COMPLETED):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot upload invoice in {filing.status.value} state. Filing must be in FILING, PAYMENT, or COMPLETED.",
        )
```

---

## Prompt 5 — Analytics Dashboards: Partner + Executive + Client (Comprehensive)

### Context & References

**Files to modify:**
- `backend/app/api/v1/dashboard.py` — add new analytics endpoints
- `backend/app/schemas/dashboard.py` — add analytics response schemas

**Existing dashboard endpoints:**
- `GET /dashboard/summary` — counters for Partner/Executive
- `GET /dashboard/pending-verification` — Partner only
- `GET /dashboard/filings-by-status` — drill-down by status
- `GET /dashboard/executive-workload` — Partner only
- `GET /dashboard/client` — client dashboard
- `GET /dashboard/directory/{filing_id}` — filing directory view

**Database tables available for analytics:**
- `users` — all users (Partner, Executive, Client)
- `itr_filings` — all filings with status, timestamps, FY
- `filing_documents` — document placeholders with status
- `filing_computations` — computation versions
- `filing_completed_docs` — acknowledgement, invoice, ITR JSON
- `filing_state_history` — all state transitions with timestamps
- `executive_client_assignments` — executive ↔ client mapping
- `client_profiles` — onboarding data
- `notifications` — all notifications

### Task 1 — Partner Analytics Dashboard

**What to do:**

Add new endpoint `GET /dashboard/analytics/partner` with comprehensive analytics:

```python
@router.get("/analytics/partner")
async def get_partner_analytics(
    current_user: User = Depends(get_current_partner),
    db: AsyncSession = Depends(get_db),
):
```

**Response schema** — add to `schemas/dashboard.py`:

```python
class ExecutiveClientDetail(BaseModel):
    executive_id: UUID
    executive_name: str
    executive_email: str
    is_active: bool
    clients: list["ExecutiveClientInfo"]
    total_clients: int
    active_filings: int
    completed_filings: int

class ExecutiveClientInfo(BaseModel):
    client_id: UUID
    client_name: str
    client_email: str
    filing_status: Optional[str] = None
    financial_year: Optional[str] = None

class ClientStatusBreakdown(BaseModel):
    status: str
    count: int

class FilingStatusBreakdown(BaseModel):
    status: str
    count: int
    clients: list["FilingStatusClientInfo"]

class FilingStatusClientInfo(BaseModel):
    client_id: UUID
    client_name: str
    financial_year: str
    assigned_executive: Optional[str] = None
    last_updated: Optional[datetime] = None

class FYDistribution(BaseModel):
    financial_year: str
    total_filings: int
    completed: int
    active: int

class PartnerAnalyticsResponse(BaseModel):
    # Overview
    total_clients: int
    active_clients: int
    pending_verification_clients: int
    rejected_clients: int
    total_executives: int
    active_executives: int

    # Filing overview
    total_filings: int
    active_filings: int
    completed_filings: int
    halted_filings: int

    # Executive → Client mapping
    executive_client_mapping: list[ExecutiveClientDetail]
    unassigned_clients: list[ExecutiveClientInfo]

    # State-wise breakdown with client details
    filing_status_breakdown: list[FilingStatusBreakdown]

    # Client account status breakdown
    client_status_breakdown: list[ClientStatusBreakdown]

    # Financial year distribution
    fy_distribution: list[FYDistribution]

    # Average processing times (in days)
    avg_days_initiated_to_completed: Optional[float] = None
    avg_days_in_processing: Optional[float] = None
    avg_days_in_computation: Optional[float] = None

    # Recent activity
    recent_filings: list[FilingStatusClientInfo]  # Last 10 state changes
```

**Implementation logic:**

```python
@router.get("/analytics/partner", response_model=PartnerAnalyticsResponse)
async def get_partner_analytics(
    current_user: User = Depends(get_current_partner),
    db: AsyncSession = Depends(get_db),
):
    """Comprehensive analytics dashboard for Partner."""
    from sqlalchemy import case, extract, func

    # ── Client counts by account status ──
    client_status_result = await db.execute(
        select(User.account_status, func.count(User.id))
        .where(User.role == UserRole.CLIENT)
        .group_by(User.account_status)
    )
    client_status_map = {row[0].value: row[1] for row in client_status_result.all()}
    total_clients = sum(client_status_map.values())
    active_clients = client_status_map.get("ACTIVE", 0)
    pending_clients = client_status_map.get("PENDING_VERIFICATION", 0)
    rejected_clients = client_status_map.get("REJECTED", 0)

    client_status_breakdown = [
        ClientStatusBreakdown(status=status, count=count)
        for status, count in client_status_map.items()
    ]

    # ── Executive counts ──
    exec_count_result = await db.execute(
        select(
            func.count(User.id).filter(User.is_active == True).label("active"),
            func.count(User.id).label("total"),
        ).where(User.role == UserRole.EXECUTIVE)
    )
    exec_counts = exec_count_result.one()

    # ── Filing counts by status ──
    filing_status_result = await db.execute(
        select(ITRFiling.status, func.count(ITRFiling.id))
        .group_by(ITRFiling.status)
    )
    filing_status_map = {row[0].value: row[1] for row in filing_status_result.all()}
    total_filings = sum(filing_status_map.values())
    completed_filings = filing_status_map.get("COMPLETED", 0)
    halted_filings = filing_status_map.get("HALTED", 0)
    active_filings = total_filings - completed_filings - halted_filings

    # ── Filing status breakdown with client details ──
    filing_status_breakdown = []
    for fs in FilingStatus:
        filings_result = await db.execute(
            select(ITRFiling).where(ITRFiling.status == fs).order_by(ITRFiling.updated_at.desc()).limit(50)
        )
        filings = filings_result.scalars().all()
        clients_info = []
        for f in filings:
            c_result = await db.execute(select(User.full_name).where(User.id == f.client_id))
            c_name = c_result.scalar() or "Unknown"
            e_name = None
            if f.assigned_executive_id:
                e_result = await db.execute(select(User.full_name).where(User.id == f.assigned_executive_id))
                e_name = e_result.scalar()
            clients_info.append(FilingStatusClientInfo(
                client_id=f.client_id,
                client_name=c_name,
                financial_year=f.financial_year,
                assigned_executive=e_name,
                last_updated=f.updated_at,
            ))
        filing_status_breakdown.append(FilingStatusBreakdown(
            status=fs.value,
            count=filing_status_map.get(fs.value, 0),
            clients=clients_info,
        ))

    # ── Executive → Client mapping ──
    exec_result = await db.execute(
        select(User).where(User.role == UserRole.EXECUTIVE).order_by(User.full_name)
    )
    executives = exec_result.scalars().all()

    executive_client_mapping = []
    for ex in executives:
        assign_result = await db.execute(
            select(ExecutiveClientAssignment).where(
                ExecutiveClientAssignment.executive_id == ex.id,
                ExecutiveClientAssignment.is_active == True,
            )
        )
        assignments = assign_result.scalars().all()
        clients_list = []
        for a in assignments:
            cl_result = await db.execute(select(User).where(User.id == a.client_id))
            cl = cl_result.scalar_one_or_none()
            if cl:
                # Get latest active filing
                fl_result = await db.execute(
                    select(ITRFiling).where(
                        ITRFiling.client_id == cl.id,
                        ITRFiling.status.notin_([FilingStatus.COMPLETED, FilingStatus.HALTED]),
                    ).order_by(ITRFiling.updated_at.desc()).limit(1)
                )
                fl = fl_result.scalar_one_or_none()
                clients_list.append(ExecutiveClientInfo(
                    client_id=cl.id,
                    client_name=cl.full_name,
                    client_email=cl.email,
                    filing_status=fl.status.value if fl else None,
                    financial_year=fl.financial_year if fl else None,
                ))

        # Filing counts for this executive
        exec_active_result = await db.execute(
            select(func.count()).select_from(ITRFiling).where(
                ITRFiling.assigned_executive_id == ex.id,
                ITRFiling.status.notin_([FilingStatus.COMPLETED, FilingStatus.HALTED]),
            )
        )
        exec_completed_result = await db.execute(
            select(func.count()).select_from(ITRFiling).where(
                ITRFiling.assigned_executive_id == ex.id,
                ITRFiling.status == FilingStatus.COMPLETED,
            )
        )

        executive_client_mapping.append(ExecutiveClientDetail(
            executive_id=ex.id,
            executive_name=ex.full_name,
            executive_email=ex.email,
            is_active=ex.is_active,
            clients=clients_list,
            total_clients=len(clients_list),
            active_filings=exec_active_result.scalar() or 0,
            completed_filings=exec_completed_result.scalar() or 0,
        ))

    # ── Unassigned clients ──
    assigned_ids_result = await db.execute(
        select(ExecutiveClientAssignment.client_id).where(ExecutiveClientAssignment.is_active == True)
    )
    assigned_ids = {row[0] for row in assigned_ids_result.all()}

    unassigned_result = await db.execute(
        select(User).where(
            User.role == UserRole.CLIENT,
            User.account_status == AccountStatus.ACTIVE,
            User.id.notin_(assigned_ids) if assigned_ids else True,
        )
    )
    unassigned_clients = [
        ExecutiveClientInfo(client_id=u.id, client_name=u.full_name, client_email=u.email)
        for u in unassigned_result.scalars().all()
    ]

    # ── FY Distribution ──
    fy_result = await db.execute(
        select(
            ITRFiling.financial_year,
            func.count(ITRFiling.id).label("total"),
            func.count(ITRFiling.id).filter(ITRFiling.status == FilingStatus.COMPLETED).label("completed"),
        )
        .group_by(ITRFiling.financial_year)
        .order_by(ITRFiling.financial_year.desc())
    )
    fy_distribution = [
        FYDistribution(
            financial_year=row.financial_year,
            total_filings=row.total,
            completed=row.completed,
            active=row.total - row.completed,
        )
        for row in fy_result.all()
    ]

    # ── Average processing times ──
    avg_completion_result = await db.execute(
        select(
            func.avg(
                extract('epoch', ITRFiling.completed_at - ITRFiling.initiated_at) / 86400
            )
        ).where(ITRFiling.completed_at.isnot(None))
    )
    avg_days_total = avg_completion_result.scalar()

    avg_processing_result = await db.execute(
        select(
            func.avg(
                extract('epoch', ITRFiling.documents_approved_at - ITRFiling.documents_submitted_at) / 86400
            )
        ).where(
            ITRFiling.documents_approved_at.isnot(None),
            ITRFiling.documents_submitted_at.isnot(None),
        )
    )
    avg_days_processing = avg_processing_result.scalar()

    avg_computation_result = await db.execute(
        select(
            func.avg(
                extract('epoch', ITRFiling.computation_approved_at - ITRFiling.computation_uploaded_at) / 86400
            )
        ).where(
            ITRFiling.computation_approved_at.isnot(None),
            ITRFiling.computation_uploaded_at.isnot(None),
        )
    )
    avg_days_computation = avg_computation_result.scalar()

    # ── Recent filings (last 10 state changes) ──
    recent_result = await db.execute(
        select(ITRFiling).order_by(ITRFiling.updated_at.desc()).limit(10)
    )
    recent_filings = []
    for f in recent_result.scalars().all():
        c_result = await db.execute(select(User.full_name).where(User.id == f.client_id))
        c_name = c_result.scalar() or "Unknown"
        e_name = None
        if f.assigned_executive_id:
            e_result = await db.execute(select(User.full_name).where(User.id == f.assigned_executive_id))
            e_name = e_result.scalar()
        recent_filings.append(FilingStatusClientInfo(
            client_id=f.client_id,
            client_name=c_name,
            financial_year=f.financial_year,
            assigned_executive=e_name,
            last_updated=f.updated_at,
        ))

    return PartnerAnalyticsResponse(
        total_clients=total_clients,
        active_clients=active_clients,
        pending_verification_clients=pending_clients,
        rejected_clients=rejected_clients,
        total_executives=exec_counts.total,
        active_executives=exec_counts.active,
        total_filings=total_filings,
        active_filings=active_filings,
        completed_filings=completed_filings,
        halted_filings=halted_filings,
        executive_client_mapping=executive_client_mapping,
        unassigned_clients=unassigned_clients,
        filing_status_breakdown=filing_status_breakdown,
        client_status_breakdown=client_status_breakdown,
        fy_distribution=fy_distribution,
        avg_days_initiated_to_completed=round(avg_days_total, 1) if avg_days_total else None,
        avg_days_in_processing=round(avg_days_processing, 1) if avg_days_processing else None,
        avg_days_in_computation=round(avg_days_computation, 1) if avg_days_computation else None,
        recent_filings=recent_filings,
    )
```

### Task 2 — Executive Analytics Dashboard

Add `GET /dashboard/analytics/executive`:

```python
class ExecutiveAnalyticsResponse(BaseModel):
    executive_name: str
    executive_email: str

    # Client overview
    total_assigned_clients: int
    clients: list[ExecutiveClientInfo]

    # Filing overview
    total_filings: int
    active_filings: int
    completed_filings: int
    halted_filings: int

    # State-wise breakdown (only assigned clients)
    filing_status_breakdown: list[FilingStatusBreakdown]

    # FY distribution
    fy_distribution: list[FYDistribution]

    # Processing metrics
    avg_days_initiated_to_completed: Optional[float] = None
    avg_days_in_processing: Optional[float] = None

    # Document stats
    total_documents_pending: int = 0
    total_documents_rejected: int = 0
    total_documents_approved: int = 0

    # Recent activity
    recent_filings: list[FilingStatusClientInfo]
```

Implementation mirrors Partner analytics but scoped to `ITRFiling.assigned_executive_id == current_user.id`.

```python
@router.get("/analytics/executive", response_model=ExecutiveAnalyticsResponse)
async def get_executive_analytics(
    current_user: User = Depends(get_current_executive_or_partner),
    db: AsyncSession = Depends(get_db),
):
    """Analytics dashboard for Executive (scoped to assigned clients)."""
    # If partner is viewing, they need to pass executive_id as query param
    # For executive, use their own id
    executive_id = current_user.id
    if current_user.role == UserRole.PARTNER:
        from fastapi import Query as Q
        # Partner can view any executive's analytics — handled separately
        pass

    # ... (mirror Partner analytics logic, filtered by assigned_executive_id == executive_id)
    # Include: assigned clients list, filing counts, status breakdown, document stats
```

Full implementation should query:
1. All clients assigned via `ExecutiveClientAssignment` where `executive_id = current_user.id` and `is_active = True`
2. All filings where `assigned_executive_id = current_user.id`
3. Group by status, FY, compute averages
4. Document stats across all assigned filings (aggregate `FilingDocument` counts by status)

### Task 3 — Client Analytics Dashboard

Add `GET /dashboard/analytics/client`:

```python
class ClientFilingDetail(BaseModel):
    filing_id: UUID
    financial_year: str
    status: str
    progress_percentage: int
    initiated_at: datetime
    completed_at: Optional[datetime] = None
    last_updated: datetime
    assigned_executive_name: Optional[str] = None
    documents_total: int = 0
    documents_approved: int = 0
    documents_pending: int = 0
    documents_rejected: int = 0
    computation_status: Optional[str] = None
    days_since_initiated: int = 0

class ClientAnalyticsResponse(BaseModel):
    # Profile
    client_name: str
    client_email: str
    account_status: str
    registered_at: datetime
    pan_number: Optional[str] = None

    # Filing overview
    total_filings: int
    active_filings: int
    completed_filings: int

    # Detailed filing list
    filings: list[ClientFilingDetail]

    # Notifications
    total_notifications: int
    unread_notifications: int

    # Document overview across all filings
    total_documents: int
    total_approved: int
    total_pending: int
    total_rejected: int
```

```python
@router.get("/analytics/client", response_model=ClientAnalyticsResponse)
async def get_client_analytics(
    current_user: User = Depends(get_current_active_client),
    db: AsyncSession = Depends(get_db),
):
    """Analytics dashboard for Client (own data only)."""
    from datetime import datetime

    # Profile
    profile_result = await db.execute(
        select(ClientProfile).where(ClientProfile.user_id == current_user.id)
    )
    profile = profile_result.scalar_one_or_none()

    # All filings
    filings_result = await db.execute(
        select(ITRFiling).where(ITRFiling.client_id == current_user.id)
        .order_by(ITRFiling.financial_year.desc())
    )
    filings = filings_result.scalars().all()

    filing_details = []
    total_docs = total_approved = total_pending = total_rejected = 0

    for f in filings:
        # Document counts
        doc_counts = await db.execute(
            select(
                func.count(FilingDocument.id).label("total"),
                func.count(FilingDocument.id).filter(FilingDocument.status == DocumentStatus.APPROVED).label("approved"),
                func.count(FilingDocument.id).filter(FilingDocument.status == DocumentStatus.PENDING_UPLOAD).label("pending"),
                func.count(FilingDocument.id).filter(FilingDocument.status == DocumentStatus.REJECTED).label("rejected"),
            ).where(FilingDocument.filing_id == f.id)
        )
        counts = doc_counts.one()
        total_docs += counts.total or 0
        total_approved += counts.approved or 0
        total_pending += counts.pending or 0
        total_rejected += counts.rejected or 0

        # Executive name
        exec_name = None
        if f.assigned_executive_id:
            e_result = await db.execute(select(User.full_name).where(User.id == f.assigned_executive_id))
            exec_name = e_result.scalar()

        # Latest computation status
        comp_result = await db.execute(
            select(FilingComputation.status).where(
                FilingComputation.filing_id == f.id
            ).order_by(FilingComputation.version.desc()).limit(1)
        )
        comp_status = comp_result.scalar()

        days_since = (datetime.utcnow() - f.initiated_at.replace(tzinfo=None)).days if f.initiated_at else 0

        filing_details.append(ClientFilingDetail(
            filing_id=f.id,
            financial_year=f.financial_year,
            status=f.status.value,
            progress_percentage=calculate_progress_percentage(f.status),
            initiated_at=f.initiated_at,
            completed_at=f.completed_at,
            last_updated=f.updated_at,
            assigned_executive_name=exec_name,
            documents_total=counts.total or 0,
            documents_approved=counts.approved or 0,
            documents_pending=counts.pending or 0,
            documents_rejected=counts.rejected or 0,
            computation_status=comp_status.value if comp_status else None,
            days_since_initiated=days_since,
        ))

    # Notification counts
    notif_result = await db.execute(
        select(
            func.count(Notification.id).label("total"),
            func.count(Notification.id).filter(Notification.is_read == False).label("unread"),
        ).where(Notification.user_id == current_user.id)
    )
    notif_counts = notif_result.one()

    completed_count = len([f for f in filings if f.status == FilingStatus.COMPLETED])

    return ClientAnalyticsResponse(
        client_name=current_user.full_name,
        client_email=current_user.email,
        account_status=current_user.account_status.value,
        registered_at=current_user.created_at,
        pan_number=profile.pan_number if profile else None,
        total_filings=len(filings),
        active_filings=len(filings) - completed_count,
        completed_filings=completed_count,
        filings=filing_details,
        total_notifications=notif_counts.total or 0,
        unread_notifications=notif_counts.unread or 0,
        total_documents=total_docs,
        total_approved=total_approved,
        total_pending=total_pending,
        total_rejected=total_rejected,
    )
```

---

## Quick Reference: File → Task Mapping

| File | Prompts |
|---|---|
| `app/enums.py` | P3 (COMPUTATION→PROCESSING), P4 (ITR_JSON enum) |
| `app/core/exceptions.py` | P1, P2, P3 |
| `app/models/onboarding_form_field.py` | P1 |
| `app/models/filing_completed_doc.py` | P4 |
| `app/api/v1/filings.py` | P1 (filing gate), P2 (exec check), P3 (backward transition), P4 (mark-payment) |
| `app/api/v1/onboarding.py` | P1 (delete fix, create fix, sanity checks) |
| `app/api/v1/documents.py` | P2 (exec check), P2 (MinIO path) |
| `app/api/v1/computations.py` | P2 (MinIO path), P3 (approval loop) |
| `app/api/v1/storage.py` | P2 (MinIO path), P4 (3 docs, visibility) |
| `app/api/v1/dashboard.py` | P4 (visibility), P5 (all 3 analytics) |
| `app/schemas/dashboard.py` | P5 (analytics schemas) |
| `app/services/filing_service.py` | P2 (exec error), P3 (backward timestamps) |
| `app/services/storage_service.py` | P2 (Name_UUID path) |
| `app/services/client_service.py` | P2 (activation fix) |
