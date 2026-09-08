import frappe
import qrcode
import io
import base64
from urllib.parse import urlencode


def generate_qr(data):
    if not data:
        return ""

    img = qrcode.make(str(data))

    buffer = io.BytesIO()
    img.save(buffer, format="PNG")

    encoded = base64.b64encode(buffer.getvalue()).decode("utf-8")

    return "data:image/png;base64," + encoded

def generate_upi_qr(amount, message):
    settings = frappe.get_single("Revive Settings")

    upi_id = settings.upi_id
    upi_name = settings.upi_name

    if not upi_id or not upi_name:
        return ""

    params = {
        "pa": upi_id,
        "pn": upi_name,
        "am": f"{float(amount):.2f}",
        "tn": message,
        "cu": "INR",
    }

    upi_url = "upi://pay?" + urlencode(params)

    img = qrcode.make(upi_url)

    buffer = io.BytesIO()
    img.save(buffer, format="PNG")

    encoded = base64.b64encode(buffer.getvalue()).decode("utf-8")

    return "data:image/png;base64," + encoded

