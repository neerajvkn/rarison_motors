import frappe


def validate_item(doc, method=None):
    if not doc.is_new():
        return

    if doc.item_group != "Services":
        return

    settings = frappe.get_single("Revive Settings")

    sequence = frappe.utils.cint(settings.service_item_sequence)

    if sequence < 1:
        sequence = 1

    # Find the first unused service item code
    while True:
        item_code = f"SVC{sequence:04d}"

        if not frappe.db.exists("Item", item_code):
            break

        sequence += 1

    # Force the correct Item Code
    doc.item_code = item_code

    # Item name should also be the Item Code
    doc.name = item_code

    # Advance the sequence
    settings.service_item_sequence = sequence + 1
    settings.save(ignore_permissions=True)