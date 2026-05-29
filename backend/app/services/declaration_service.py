"""Service — Generate Confidentiality & Data Protection Declaration PDF."""

from datetime import datetime
from typing import Optional

from fpdf import FPDF


DECLARATION_TITLE = "Confidentiality & Data Protection Declaration"

DECLARATION_BODY = """We are committed to safeguarding the confidentiality, privacy, and security of your personal and financial information. All documents and information shared with us for Income Tax Return ("ITR") filing and related professional services shall be handled with due care, professional confidentiality, and appropriate security safeguards in accordance with applicable laws and professional standards.

1. All information and documents submitted by you including, but not limited to, PAN, Aadhaar, bank account details, income details, investment proofs, tax documents, financial statements, and supporting records shall be used strictly for the purpose of preparation, verification, processing, filing, and compliance relating to Income Tax Returns and allied professional services.

2. Your information shall not be used, shared, disclosed, sold, transferred, or circulated for any unrelated marketing or commercial purpose except:
   - where required under applicable law or regulatory requirements;
   - where specifically authorized by you; or
   - where required for statutory compliance with government authorities, portals, intermediaries, or authorized service providers involved in the filing or compliance process.

3. All client data and documents are stored securely within the firm's controlled internal infrastructure, including secure storage systems and restricted-access environments, and are protected through appropriate technical and organizational safeguards.

4. Client documents and confidential information shall not knowingly be uploaded to any publicly accessible repository or unsecured external platform.

5. Access to client information is restricted only to authorized personnel, employees, consultants, or professionals associated with the firm on a strict need-to-know basis for carrying out the intended professional services.

6. Reasonable security measures including access controls, authentication mechanisms, encryption practices, backup procedures, and monitoring systems are implemented to safeguard information against unauthorized access, alteration, disclosure, loss, or misuse.

7. Client data and records may be retained for such period as may be necessary for professional, legal, regulatory, audit, documentation, or compliance purposes and may thereafter be securely archived or deleted in accordance with applicable requirements and internal policies."""

RISK_DISCLAIMER_TITLE = "Risk & Limitation Disclaimer"

RISK_DISCLAIMER_BODY = """While reasonable industry-standard safeguards and security controls are maintained, no digital system, internet-based transmission, or electronic storage mechanism can guarantee absolute security. Accordingly, the firm shall not be liable for any indirect or consequential loss arising from unauthorized access, cyberattacks, malware, service interruptions, internet failures, governmental actions, force majeure events, or unauthorized acts of third parties beyond the firm's reasonable control, despite implementation of reasonable safeguards."""

CONSENT_TITLE = "Consent"

CONSENT_BODY = """I/We have read and understood the above Confidentiality & Data Protection Declaration and hereby consent to the collection, storage, processing, retention, and use of the information and documents provided by me/us solely for the purpose of ITR filing, verification, compliance, and related professional services."""


def generate_declaration_pdf(
    full_name: str,
    email: str,
    phone_number: Optional[str],
    accepted_at: datetime,
) -> bytes:
    """
    Generate a PDF of the Confidentiality & Data Protection Declaration
    with the user's details and consent timestamp.

    Returns the PDF content as bytes.
    """
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()

    # Title
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, DECLARATION_TITLE, new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.ln(6)

    # Declaration body
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(0, 5, DECLARATION_BODY)
    pdf.ln(8)

    # Risk Disclaimer
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, RISK_DISCLAIMER_TITLE, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(0, 5, RISK_DISCLAIMER_BODY)
    pdf.ln(8)

    # Consent section
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, CONSENT_TITLE, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(0, 5, CONSENT_BODY)
    pdf.ln(10)

    # Separator
    pdf.set_draw_color(100, 100, 100)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(8)

    # User details
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, "Signatory Details", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    pdf.set_font("Helvetica", "", 10)
    pdf.cell(40, 6, "Name:")
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 6, full_name, new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "", 10)
    pdf.cell(40, 6, "Email:")
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 6, email, new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "", 10)
    pdf.cell(40, 6, "Mobile Number:")
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 6, phone_number or "Not provided", new_x="LMARGIN", new_y="NEXT")

    pdf.ln(4)
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(40, 6, "Consent Given On:")
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 6, accepted_at.strftime("%d %B %Y, %I:%M %p IST"), new_x="LMARGIN", new_y="NEXT")

    pdf.ln(6)
    pdf.set_font("Helvetica", "I", 9)
    pdf.cell(0, 5, "This document was generated electronically and does not require a physical signature.", new_x="LMARGIN", new_y="NEXT")

    return pdf.output()
