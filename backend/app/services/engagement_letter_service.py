"""Service — Generate Engagement Letter PDF and upload to MinIO."""

import io
from datetime import datetime
from decimal import Decimal
from typing import Optional

from fpdf import FPDF

from app.config import settings
from app.services.storage_service import _get_client, build_client_dir, ensure_bucket_exists


FIRM_NAME = "P G Joshi and Co LLP"

ENGAGEMENT_LETTER_BODY = """This Engagement Letter sets out the terms and conditions governing the professional services to be provided by {firm_name} ("the Firm") to the Client for Income Tax Return ("ITR") filing and related tax compliance services.

1. Scope of Services

The Firm shall provide professional services including:

- Preparation and filing of Income Tax Return(s) for income under the heads:
  1. Salary
  2. House property
  3. Capital Gains
  4. Profits and Gains from Business and Profession
  5. Other Sources
- Computation of taxable income and tax liability based on information provided by the Client;
- Assistance in tax filing compliance and related procedural matters;
- Basic clarification and communication relating to the filed return;
- Assistance in responding to routine processing-related queries, wherever applicable.

The scope of services shall be limited to the relevant financial year and assignment specifically agreed upon.

2. Client Responsibilities

The Client shall:

- Provide complete, accurate, and timely information, records, and supporting documents including PAN, Aadhaar, bank details, income details, investment proofs, tax statements, and other relevant information;
- Ensure authenticity and correctness of the information submitted;
- Review the computation and draft return shared by the Firm before filing;
- Provide necessary approvals, consents, OTPs, e-verification authorizations, or confirmations required for filing and compliance purposes.

The Firm shall rely upon the information and documents provided by the Client and shall not be responsible for any consequences arising from incomplete, incorrect, inaccurate, or delayed information.

3. Confidentiality & Data Protection

The Firm shall maintain confidentiality of all information and documents shared by the Client and shall use such information strictly for the purpose of ITR filing and related professional services.

Client data shall be stored securely within the Firm's controlled internal infrastructure, including secure storage systems such as MinIO, and shall not be uploaded to any public server or unsecured public platform.

Access to client information shall be restricted to authorized personnel associated with the assignment on a need-to-know basis.

Reasonable security safeguards and technical measures shall be maintained to protect client information against unauthorized access, disclosure, alteration, or misuse.

4. Limitation of Responsibility

The Firm shall exercise reasonable professional care in providing the services. However:

- The Firm shall not be responsible for errors, penalties, notices, or consequences arising due to incorrect, incomplete, or delayed information provided by the Client;
- The Firm shall not be liable for delays or failures caused by technical issues, portal downtime, governmental systems, third-party intermediaries, cyber incidents, or events beyond reasonable control;
- No assurance is provided regarding selection of return for scrutiny, assessment, or verification proceedings by tax authorities.

{fee_sections}

{acceptance_section_number}. Acceptance & Consent

By proceeding with the engagement, submitting information/documents through the platform, or accepting these terms electronically, the Client:

- confirms that the information provided is true and complete to the best of their knowledge;
- consents to the collection, storage, and processing of information for the purpose of providing the agreed services; and
- agrees to the terms contained in this Engagement Letter."""

FEE_SECTIONS_TEMPLATE = """5. Professional Fees

The professional fee for the above services shall be:

{fee_line}

Any additional work outside the agreed scope including notices, scrutiny matters, rectifications, appeals, or advisory services may be charged separately based on the nature and extent of work involved.

6. Payment Terms

Fees shall be payable upon acceptance of this Engagement Letter and/or prior to filing of the return unless otherwise agreed.

The Firm reserves the right to withhold filing, submission, or delivery of services in case of non-payment of fees."""

NO_FEE_SECTIONS_TEMPLATE = """5. Professional Fees

No professional fees are applicable for this engagement."""


def generate_engagement_letter_pdf(
    client_name: str,
    financial_year: str,
    professional_fee: Optional[Decimal],
    accepted_at: datetime,
    no_fees_applicable: bool = False,
) -> bytes:
    """Generate the engagement letter PDF with client details and fee filled in."""
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()

    # Title
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, "Engagement Letter for Income Tax Return Filing Services", new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.ln(4)

    # Financial Year
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, f"Financial Year: {financial_year}", new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.ln(6)

    # Build fee sections based on no_fees_applicable flag
    if no_fees_applicable:
        fee_sections = NO_FEE_SECTIONS_TEMPLATE
        acceptance_section_number = "6"
    else:
        fee_str = f"Rs. {professional_fee:,.2f} plus applicable taxes, if any."
        fee_sections = FEE_SECTIONS_TEMPLATE.format(fee_line=fee_str)
        acceptance_section_number = "7"

    # Body
    body = ENGAGEMENT_LETTER_BODY.format(
        firm_name=FIRM_NAME,
        fee_sections=fee_sections,
        acceptance_section_number=acceptance_section_number,
    )

    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(0, 5, body)
    pdf.ln(10)

    # Firm signature section
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, f"For {FIRM_NAME}", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, "Authorized Signatory", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 6, f"Date: {accepted_at.strftime('%d %B %Y')}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(10)

    # Separator
    pdf.set_draw_color(100, 100, 100)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(8)

    # Client acceptance
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "Client Acceptance", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(0, 5, f"I/We have read and understood the terms of this Engagement Letter and agree to appoint {FIRM_NAME} for the above services.")
    pdf.ln(6)

    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 6, "[X] I Agree to the Terms of Engagement", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    pdf.set_font("Helvetica", "", 10)
    pdf.cell(30, 6, "Name:")
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 6, client_name, new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "", 10)
    pdf.cell(30, 6, "Date:")
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 6, accepted_at.strftime("%d %B %Y, %I:%M %p IST"), new_x="LMARGIN", new_y="NEXT")

    pdf.ln(8)
    pdf.set_font("Helvetica", "I", 9)
    pdf.cell(0, 5, "This document was generated electronically and does not require a physical signature.", new_x="LMARGIN", new_y="NEXT")

    return pdf.output()


def upload_engagement_letter(
    client_id: str,
    client_name: str,
    financial_year: str,
    pdf_bytes: bytes,
) -> str:
    """Upload engagement letter PDF to MinIO. Returns the object key."""
    client_dir = build_client_dir(client_id, client_name)
    object_key = f"clients/{client_dir}/ITR-{financial_year}/engagement_letter.pdf"

    minio_client = _get_client()
    ensure_bucket_exists()
    minio_client.put_object(
        bucket_name=settings.MINIO_BUCKET_NAME,
        object_name=object_key,
        data=io.BytesIO(pdf_bytes),
        length=len(pdf_bytes),
        content_type="application/pdf",
    )
    return object_key
