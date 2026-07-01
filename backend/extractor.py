import pdfplumber
import anthropic
import base64
import json
import io
import re
from datetime import datetime
from pdf2image import convert_from_bytes  # pip install pdf2image
from dotenv import load_dotenv
load_dotenv() 

client = anthropic.Anthropic()


def extract_text_from_pdf_bytes(pdf_bytes: bytes) -> list[str]:
    """Try text extraction first; return empty strings for image-based pages."""
    page_texts = []
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ""
                page_texts.append(text)
    except Exception as e:
        page_texts.append(f"ERROR: {str(e)}")
    return page_texts


def _pdf_to_base64_images(pdf_bytes: bytes) -> list[str]:
    """Convert each PDF page to a base64 PNG string."""
    images = convert_from_bytes(pdf_bytes, dpi=200)
    result = []
    for img in images:
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        result.append(base64.standard_b64encode(buf.getvalue()).decode("utf-8"))
    return result


def _call_claude_text(system: str, user_content: str) -> str:
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        system=system,
        messages=[{"role": "user", "content": user_content}]
    )
    return response.content[0].text


def _call_claude_vision(system: str, b64_images: list[str]) -> str:
    """Send PDF pages as images to Claude vision."""
    content = []
    for b64 in b64_images:
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": b64}
        })
    content.append({
        "type": "text",
        "text": "Extract all the invoice/payment data from these PDF page images."
    })
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        system=system,
        messages=[{"role": "user", "content": content}]
    )
    return response.content[0].text


def _parse_json_response(text: str) -> any:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE)
    return json.loads(cleaned)


def _is_scanned(pages: list[str]) -> bool:
    """True if the PDF has no usable text (scanned/image-based)."""
    total_text = "".join(pages).strip()
    return len(total_text) < 50


# ── System prompts (unchanged) ───────────────────────────────────────────────

INVOICE_SYSTEM = """You are an expert at extracting structured data from Indian GST invoices.
Extract ALL invoices found. For each invoice return a JSON object with these exact keys:
- gstin: string (15-char Indian GSTIN of the SUPPLIER/VENDOR, not buyer)
- vendor_name: string (supplier/vendor company name)
- invoice_no: string
- date: string (as it appears)
- taxable_amount: number (0 if not found)
- cgst: number (0 if not found)
- sgst: number (0 if not found)
- igst: number (0 if not found)
- total_amount: number (grand total)

Return ONLY a JSON array. No explanation, no markdown fences.
If total_amount cannot be found, skip that invoice. Numbers must be plain floats."""

PAYMENT_SYSTEM = """You are an expert at extracting vendor payment records from Indian auditor/payment reports.

Extract ONLY vendor-level payment entries — NOT line items, products, or individual goods.
A valid entry is a company or person who received a payment.

For each vendor payment return a JSON object with:
- gstin: string (15-char Indian GSTIN if present, otherwise null)
- vendor_name: string (the company or person name — must look like a business name, not a product description)
- amount_paid: number (total INR amount paid to this vendor)

Rules:
- Return ONLY a JSON array. No explanation, no markdown fences.
- SKIP any entry where vendor_name looks like a product, item description, or line item (e.g. contains words like "NOS", "KG", "PCS", "units", numeric quantities mid-name).
- SKIP entries where amount_paid cannot be clearly determined.
- If the same vendor appears multiple times, sum their amounts into one entry.
- Numbers must be plain floats, no commas or currency symbols."""


# ── Public functions ─────────────────────────────────────────────────────────

def parse_invoices_from_text(pages: list[str], source_file: str, pdf_bytes: bytes = None) -> list[dict]:
    try:
        if _is_scanned(pages) and pdf_bytes:
            # Vision path for scanned PDFs
            b64_images = _pdf_to_base64_images(pdf_bytes)
            raw = _call_claude_vision(INVOICE_SYSTEM, b64_images)
        else:
            # Text path for digital PDFs
            full_text = "\n\n--- PAGE BREAK ---\n\n".join(pages).strip()
            raw = _call_claude_text(INVOICE_SYSTEM, f"Extract all invoices:\n\n{full_text}")

        records = _parse_json_response(raw)
        if not isinstance(records, list):
            records = [records]
    except Exception as e:
        print(f"Claude invoice extraction failed for {source_file}: {e}")
        return []

    now = datetime.utcnow().isoformat()
    results = []
    for r in records:
        total = r.get("total_amount") or 0
        if not total:
            continue
        results.append({
            "gstin":          r.get("gstin") or "UNKNOWN",
            "vendor_name":    r.get("vendor_name") or "Unknown Vendor",
            "invoice_no":     r.get("invoice_no") or "N/A",
            "date":           r.get("date") or "N/A",
            "taxable_amount": float(r.get("taxable_amount") or 0),
            "cgst":           float(r.get("cgst") or 0),
            "sgst":           float(r.get("sgst") or 0),
            "igst":           float(r.get("igst") or 0),
            "total_amount":   float(total),
            "source_file":    source_file,
            "uploaded_at":    now,
        })
    return results


def parse_payments_from_text(pages: list[str], source_file: str, pdf_bytes: bytes = None) -> list[dict]:
    try:
        if _is_scanned(pages) and pdf_bytes:
            b64_images = _pdf_to_base64_images(pdf_bytes)
            raw = _call_claude_vision(PAYMENT_SYSTEM, b64_images)
        else:
            full_text = "\n\n--- PAGE BREAK ---\n\n".join(pages).strip()
            raw = _call_claude_text(PAYMENT_SYSTEM, f"Extract all payment records:\n\n{full_text}")

        records = _parse_json_response(raw)
        if not isinstance(records, list):
            records = [records]
    except Exception as e:
        print(f"Claude payment extraction failed for {source_file}: {e}")
        return []

    now = datetime.utcnow().isoformat()
    results = []
    for r in records:
        amt = r.get("amount_paid") or 0
        if not amt:
            continue
        gstin = r.get("gstin") or None
        vendor = r.get("vendor_name") or "Unknown Vendor"
        if not gstin:
            slug = re.sub(r"[^A-Z0-9]", "_", vendor.upper())[:10]
            gstin = f"UNKNOWN_{slug}"
        results.append({
            "gstin":        gstin,
            "vendor_name":  vendor,
            "amount_paid":  float(amt),
            "source_file":  source_file,
            "uploaded_at":  now,
        })
    return results