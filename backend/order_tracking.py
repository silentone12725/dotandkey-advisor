"""
backend/order_tracking.py

Inline order status lookup via ClickPost's customer-facing tracking API.
No API key required — same endpoint the dotandkey.clickpost.ai portal uses.

Usage:
    info = extract_tracking_info(user_message)
    if info:
        result = await lookup_order(**info)
        text = format_tracking_result(result)
"""

import re
from typing import TypedDict

import httpx

CLICKPOST_DOMAIN = "dotandkey"
CLICKPOST_BASE_URL = "https://dotandkey.clickpost.ai"
CLICKPOST_TRACK_URL = f"{CLICKPOST_BASE_URL}/api/returns?isTrackLogin=true"

# ClickPost uber_status codes → human label
_STATUS_LABELS: dict[int, str] = {
    1:  "Order placed",
    2:  "Pickup pending",
    3:  "Picked up / In transit",
    4:  "Out for delivery",
    5:  "Delivered",
    6:  "Delivery attempted — will retry",
    7:  "Cancelled",
    8:  "Delivered",          # Dot & Key config maps 8 → delivered
    9:  "Return in transit",
    10: "Return delivered",
    11: "Lost in transit",
}


class TrackingInfo(TypedDict, total=False):
    reference_number: str
    phone_number: str
    email: str
    waybill: str


# ── Extraction ────────────────────────────────────────────────────────────────

_ORDER_RE = re.compile(r'\b(\d{6,8})\b')            # 6-8 digit numeric order IDs
_PHONE_RE = re.compile(r'\b([6-9]\d{9})\b')          # Indian mobile: starts 6-9, 10 digits
_EMAIL_RE = re.compile(r'\b[\w.+-]+@[\w-]+\.\w+\b')
_AWB_RE   = re.compile(r'\b(\d{10,15})\b')           # courier AWB: 10-15 digits


def extract_tracking_info(message: str) -> TrackingInfo | None:
    """Pull order ID + phone/email from a user message.

    Returns None if we can't find at least an order ID or an AWB.
    """
    text = message.strip()

    phone_m = _PHONE_RE.search(text)
    email_m = _EMAIL_RE.search(text)
    phone   = phone_m.group(1) if phone_m else ""
    email   = email_m.group(0) if email_m else ""

    # Remove phone from text before searching for order ID so a 10-digit
    # phone isn't mistaken for an AWB.
    clean = _PHONE_RE.sub("", text)

    order_m = _ORDER_RE.search(clean)
    if order_m and (phone or email):
        return TrackingInfo(
            reference_number=order_m.group(1),
            phone_number=phone,
            email=email,
        )

    # Waybill-only lookup (10-15 digit number, no phone needed)
    awb_m = _AWB_RE.search(clean)
    if awb_m and not order_m:
        return TrackingInfo(waybill=awb_m.group(1))

    return None


# ── API call ──────────────────────────────────────────────────────────────────

async def lookup_order(
    reference_number: str = "",
    phone_number: str = "",
    email: str = "",
    waybill: str = "",
) -> dict:
    """Call ClickPost and return the parsed response dict.

    Returns {"success": bool, "items": [...], "awb": str, "error": str}
    """
    payload: dict = {"domain": CLICKPOST_DOMAIN, "email": email}
    if waybill:
        payload["waybill"] = waybill
    else:
        payload["reference_number"] = reference_number
        payload["phone_number"] = phone_number

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(
                CLICKPOST_TRACK_URL,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "Origin":  "https://dotandkey.clickpost.ai",
                    "Referer": "https://dotandkey.clickpost.ai/",
                },
            )
        data = resp.json()
    except Exception as exc:
        return {"success": False, "error": str(exc), "items": [], "awb": ""}

    meta = data.get("data", {}).get("meta", {})
    if not meta.get("success"):
        return {"success": False, "error": meta.get("message", "Lookup failed"), "items": [], "awb": ""}

    result = data["data"]["result"]
    items = [
        {
            "description": i.get("description", ""),
            "sku":         i.get("sku", ""),
            "price":       i.get("final_amount_paid") or i.get("price", 0),
            "status_code": i.get("shipment_latest_uber_status", 0),
            "status":      _STATUS_LABELS.get(i.get("shipment_latest_uber_status", 0), "In transit"),
            "cancelled":   i.get("order_cancelled", False),
            "awb":         i.get("tracking_info__awb", ""),
            "can_return":  i.get("shipment_eligible_for_return", False),
            "return_reason": i.get("non_return_reason", ""),
        }
        for i in result.get("item_info", [])
    ]
    return {
        "success": True,
        "error":   "",
        "order_id": result.get("reference_number", reference_number),
        "awb":      result.get("awb", ""),
        "items":    items,
        "order_type": result.get("order_type", ""),
    }


# ── Formatter ─────────────────────────────────────────────────────────────────

def format_tracking_result(result: dict) -> str:
    """Turn a lookup_order() result into a short, chat-friendly string."""
    if not result["success"]:
        return ""   # caller handles fallback

    items = result["items"]
    if not items:
        return f"Order #{result['order_id']} found but no shipment details available yet."

    lines = []
    for item in items:
        status = "Cancelled" if item["cancelled"] else item["status"]
        line = f"**{item['description']}** — {status}"
        if item["awb"]:
            line += f" (AWB: {item['awb']})"
        if item["can_return"]:
            line += " · Return eligible"
        elif item["return_reason"]:
            line += f" · {item['return_reason']}"
        lines.append(line)

    summary = "\n".join(lines)
    order_type = f" ({result['order_type']})" if result.get("order_type") else ""
    return f"Order #{result['order_id']}{order_type}:\n{summary}"
