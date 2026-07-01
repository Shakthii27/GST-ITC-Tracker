from database import get_invoice_totals, get_payment_totals
from difflib import SequenceMatcher

TOLERANCE = 1.0


def _name_similarity(a: str, b: str) -> float:
    a = a.lower().strip()
    b = b.lower().strip()
    return SequenceMatcher(None, a, b).ratio()


def _match_payments_to_invoices(inv_map: dict, pay_map: dict) -> dict:
    merged = {}

    for gstin, inv in inv_map.items():
        merged[gstin] = {"inv": inv, "pay": None}

    for pay_gstin, pay in pay_map.items():
        # 1. Exact GSTIN match
        if pay_gstin in merged:
            merged[pay_gstin]["pay"] = pay
            continue

        # 2. Fuzzy vendor name match — only against entries that HAVE an invoice
        best_score = 0
        best_gstin = None
        pay_name = pay.get("vendor_name", "")

        for inv_gstin, data in merged.items():
            if data["inv"] is None:          # ← skip payment-only buckets
                continue
            inv_name = data["inv"].get("vendor_name", "")
            score = _name_similarity(pay_name, inv_name)
            if score > best_score:
                best_score = score
                best_gstin = inv_gstin

        if best_score >= 0.6 and best_gstin:
            if merged[best_gstin]["pay"] is None:
                merged[best_gstin]["pay"] = pay
            else:
                merged[best_gstin]["pay"]["total_paid"] = (
                    merged[best_gstin]["pay"].get("total_paid", 0) +
                    pay.get("total_paid", 0)
                )
        else:
            merged[pay_gstin] = {"inv": None, "pay": pay}

    return merged
    """
    Returns a merged map keyed by invoice GSTIN.
    Tries exact GSTIN match first, then fuzzy vendor name match.
    """
    merged = {}  # gstin -> {inv, pay}

    # Start with all invoices
    for gstin, inv in inv_map.items():
        merged[gstin] = {"inv": inv, "pay": None}

    # Match payments
    for pay_gstin, pay in pay_map.items():
        # 1. Exact GSTIN match
        if pay_gstin in merged:
            merged[pay_gstin]["pay"] = pay
            continue

        # 2. Fuzzy vendor name match against invoice vendors
        best_score = 0
        best_gstin = None
        pay_name = pay.get("vendor_name", "")

        for inv_gstin, data in merged.items():
            inv_name = data["inv"].get("vendor_name", "")
            score = _name_similarity(pay_name, inv_name)
            if score > best_score:
                best_score = score
                best_gstin = inv_gstin

        if best_score >= 0.6 and best_gstin:
            # Merge into the matched invoice's GSTIN bucket
            if merged[best_gstin]["pay"] is None:
                merged[best_gstin]["pay"] = pay
            else:
                # Already has a payment — accumulate
                merged[best_gstin]["pay"]["total_paid"] = (
                    merged[best_gstin]["pay"].get("total_paid", 0) +
                    pay.get("total_paid", 0)
                )
        else:
            # No match — keep as standalone payment entry
            merged[pay_gstin] = {"inv": None, "pay": pay}

    return merged


def reconcile() -> dict:
    inv_map = {r["_id"]: r for r in get_invoice_totals()}
    pay_map = {r["_id"]: r for r in get_payment_totals()}

    merged = _match_payments_to_invoices(inv_map, pay_map)

    unpaid = []
    paid = []
    overpaid = []

    for gstin, data in merged.items():
        inv = data["inv"] or {}
        pay = data["pay"] or {}

        total_invoiced = inv.get("total_invoiced", 0.0)
        total_paid = pay.get("total_paid", 0.0)
        vendor_name = inv.get("vendor_name") or pay.get("vendor_name", "Unknown")
        invoice_count = inv.get("invoice_count", 0)
        payment_files = pay.get("source_files", [])
        invoices = inv.get("invoices", [])

        balance = total_paid - total_invoiced

        record = {
            "gstin": gstin,
            "vendor_name": vendor_name,
            "total_invoiced": round(total_invoiced, 2),
            "total_paid": round(total_paid, 2),
            "invoice_count": invoice_count,
            "payment_files": payment_files,
            "invoices": invoices,
        }

        if balance < -TOLERANCE:
            record["status"] = "unpaid"
            record["pending"] = round(abs(balance), 2)
            unpaid.append(record)
        elif balance > TOLERANCE:
            record["status"] = "overpaid"
            record["excess"] = round(balance, 2)
            overpaid.append(record)
        else:
            record["status"] = "paid"
            paid.append(record)

    unpaid.sort(key=lambda x: x["vendor_name"].lower())
    paid.sort(key=lambda x: x["vendor_name"].lower())
    overpaid.sort(key=lambda x: x["vendor_name"].lower())

    return {
        "unpaid": unpaid,
        "paid": paid,
        "overpaid": overpaid,
        "summary": {
            "total_vendors": len(merged),
            "unpaid_count": len(unpaid),
            "paid_count": len(paid),
            "overpaid_count": len(overpaid),
            "total_invoiced": round(sum(r["total_invoiced"] for r in unpaid + paid + overpaid), 2),
            "total_collected": round(sum(r["total_paid"] for r in unpaid + paid + overpaid), 2),
        }
    }