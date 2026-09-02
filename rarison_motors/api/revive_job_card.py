import frappe
from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry
import frappe
from frappe import _
from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

@frappe.whitelist()
def create_job_card_invoices(job_card_name):
    job_card = frappe.get_doc("Revive Job Card", job_card_name)

    # ---------------------------------------------------------
    # Basic validation
    # ---------------------------------------------------------

    if not job_card.customer:
        frappe.throw("Please select a Customer before creating an invoice.")

    spares = job_card.get("spares_used") or []
    services = job_card.get("labour_work_done") or []

    if not spares and not services:
        return {
            "success": False,
            "message": "No spares or services found. No invoices were created."
        }

    # Prevent duplicate invoices
    if job_card.spares_invoice:
        frappe.throw(
            "A Spares Sales Invoice already exists: "
            + job_card.spares_invoice
        )

    if job_card.service_invoice:
        frappe.throw(
            "A Service Sales Invoice already exists: "
            + job_card.service_invoice
        )

    created_invoices = []
    settings = frappe.get_single("Revive Settings")
    spares_series = settings.sales_invoice_series
    service_series = settings.service_invoice_series


    # ---------------------------------------------------------
    # SPARES INVOICE
    # ---------------------------------------------------------

    if spares:

        spare_invoice = frappe.new_doc("Sales Invoice")

        spare_invoice.customer = job_card.customer
        spare_invoice.job_card = job_card.name

        # Items
        for row in spares:

            if not row.item:
                frappe.throw(
                    "Item is missing in Spares row #{0}.".format(row.idx)
                )

            item = frappe.get_doc("Item", row.item)

            spare_invoice.append("items", {
                "item_code": row.item,
                "item_name": item.item_name,
                "qty": row.qty or 1,
                "rate": row.rate or 0,
                "warehouse": row.warehouse if hasattr(row, "warehouse") else None
            })

        # This invoice is unpaid by default.
        spare_invoice.is_pos = 0
        spare_invoice.naming_series = spares_series
        print(spares_series)
        spare_invoice.update_stock = 1
        spare_invoice.insert(ignore_permissions=True)
        allocate_job_card_advances(
            spare_invoice,
            job_card
        )

        spare_invoice.save(ignore_permissions=True)

        
        spare_invoice.submit()

        created_invoices.append({
            "type": "spares",
            "name": spare_invoice.name
        })

        job_card.spares_invoice = spare_invoice.name


    # ---------------------------------------------------------
    # SERVICE INVOICE
    # ---------------------------------------------------------

    if services:

        service_invoice = frappe.new_doc("Sales Invoice")

        service_invoice.customer = job_card.customer
        service_invoice.job_card = job_card.name

        for row in services:

            if not row.repair:
                frappe.throw(
                    "Repair/Service item is missing in Service row #{0}."
                    .format(row.idx)
                )

            item = frappe.get_doc("Item", row.repair)

            service_invoice.append("items", {
                "item_code": row.repair,
                "item_name": item.item_name,
                "qty": row.qty or 1,
                "rate": row.rate or 0
            })

        # Service/labour invoice should NOT maintain stock.
        for item_row in service_invoice.items:
            item_row.update_stock = 0

        service_invoice.is_pos = 0
        service_invoice.naming_series = service_series
        print(service_series)
        service_invoice.update_stock = 0

        service_invoice.insert(ignore_permissions=True)
        allocate_job_card_advances(
            service_invoice,
            job_card
        )

        service_invoice.save(ignore_permissions=True)
        service_invoice.submit()

        created_invoices.append({
            "type": "service",
            "name": service_invoice.name
        })

        job_card.service_invoice = service_invoice.name


    # ---------------------------------------------------------
    # SAVE JOB CARD
    # ---------------------------------------------------------

    job_card.save(ignore_permissions=True)

    frappe.db.commit()

    return {
        "success": True,
        "message": "Sales invoice(s) created and submitted successfully.",
        "invoices": created_invoices,
        "spares_invoice": job_card.spares_invoice,
        "service_invoice": job_card.service_invoice
    }


@frappe.whitelist()
def make_job_card_payment(job_card_name, payment_amount):

    job_card = frappe.get_doc("Revive Job Card", job_card_name)

    if not job_card.customer:
        frappe.throw(_("Customer is required."))

    try:
        payment_amount = frappe.utils.flt(payment_amount)
    except Exception:
        frappe.throw(_("Invalid payment amount."))

    if payment_amount <= 0:
        frappe.throw(_("Payment amount must be greater than zero."))

    # ---------------------------------------------------------
    # Get linked invoices
    # ---------------------------------------------------------

    invoice_names = []

    if job_card.spares_invoice:
        invoice_names.append(job_card.spares_invoice)

    if job_card.service_invoice:
        invoice_names.append(job_card.service_invoice)

    if not invoice_names:
        frappe.throw(
            _("No Sales Invoices are linked to this Job Card.")
        )

    # ---------------------------------------------------------
    # Get submitted + outstanding invoices
    # ---------------------------------------------------------

    invoices = []

    for invoice_name in invoice_names:

        invoice = frappe.get_doc("Sales Invoice", invoice_name)

        if invoice.docstatus != 1:
            frappe.throw(
                _("Sales Invoice {0} is not submitted.").format(
                    invoice.name
                )
            )

        if invoice.customer != job_card.customer:
            frappe.throw(
                _("Sales Invoice {0} belongs to a different customer.")
                .format(invoice.name)
            )

        outstanding = frappe.utils.flt(invoice.outstanding_amount)

        if outstanding > 0:
            invoices.append(invoice)

    if not invoices:
        frappe.throw(
            _("All Sales Invoices linked to this Job Card are already paid.")
        )

    # ---------------------------------------------------------
    # Validate payment amount
    # ---------------------------------------------------------

    total_outstanding = sum(
        frappe.utils.flt(invoice.outstanding_amount)
        for invoice in invoices
    )

    if payment_amount > total_outstanding:
        frappe.throw(
            _(
                "Payment amount cannot be greater than the total outstanding "
                "amount of {0}."
            ).format(frappe.utils.fmt_money(total_outstanding))
        )

    # ---------------------------------------------------------
    # Create Payment Entry using ERPNext's standard mechanism
    # ---------------------------------------------------------

    payment_entry = get_payment_entry(
        "Sales Invoice",
        invoices[0].name
    )

    # ---------------------------------------------------------
    # Set Job Card
    # ---------------------------------------------------------

    payment_entry.custom_job_card = job_card.name

    # ---------------------------------------------------------
    # Set payment amount
    # ---------------------------------------------------------

    payment_entry.paid_amount = payment_amount
    payment_entry.received_amount = payment_amount

    # ---------------------------------------------------------
    # Clear references created by get_payment_entry
    #
    # We'll rebuild them according to the amount entered.
    # ---------------------------------------------------------

    payment_entry.set("references", [])

    remaining_amount = payment_amount

    # ---------------------------------------------------------
    # Allocate payment sequentially
    # ---------------------------------------------------------

    for invoice in invoices:

        if remaining_amount <= 0:
            break

        outstanding = frappe.utils.flt(
            invoice.outstanding_amount
        )

        allocated_amount = min(
            outstanding,
            remaining_amount
        )

        if allocated_amount <= 0:
            continue


        payment_entry.append("references", {
            "reference_doctype": "Sales Invoice",
            "reference_name": invoice.name,
            "total_amount": invoice.grand_total,
            "outstanding_amount": outstanding,
            "allocated_amount": allocated_amount,
        })

        remaining_amount -= allocated_amount

    # ---------------------------------------------------------
    # Recalculate Payment Entry amounts
    # ---------------------------------------------------------

    payment_entry.set_amounts()

    # Make sure the requested amount remains the paid amount
    payment_entry.paid_amount = payment_amount
    payment_entry.received_amount = payment_amount

    # ---------------------------------------------------------
    # Save as DRAFT
    #
    # Do NOT submit here.
    # User will select Mode of Payment and submit.
    # ---------------------------------------------------------

    payment_entry.insert(ignore_permissions=True)

    return {
        "name": payment_entry.name,
        "payment_amount": payment_amount,
    }

def payment_entry_on_submit(doc, method=None):
    if not doc.custom_job_card:
        return

    job_card = frappe.get_doc(
        "Revive Job Card",
        doc.custom_job_card
    )

    payment_amount = frappe.utils.flt(
        doc.paid_amount
    )

    if payment_amount <= 0:
        return

    current_paid_amount = frappe.utils.flt(
        job_card.total_paid_amount
    )

    job_card.total_paid_amount = (
        current_paid_amount + payment_amount
    )

    job_card.save(ignore_permissions=True)

def allocate_job_card_advances(invoice, job_card):
    """
    Allocate unallocated customer advances belonging to this Job Card
    against the given Sales Invoice.

    Advances are consumed oldest-first.
    """

    if not invoice.customer:
        return

    if not job_card.name:
        return

    # ---------------------------------------------------------
    # Fetch submitted, unallocated Payment Entries
    # belonging specifically to this Job Card
    # ---------------------------------------------------------

    advances = frappe.get_all(
        "Payment Entry",
        filters={
            "payment_type": "Receive",
            "party_type": "Customer",
            "party": job_card.customer,
            "custom_job_card": job_card.name,
            "docstatus": 1,
            "unallocated_amount": [">", 0],
        },
        fields=[
            "name",
            "posting_date",
            "unallocated_amount",
            "paid_from",
            "paid_from_account_currency",
            "source_exchange_rate",
        ],
        order_by="posting_date asc, creation asc",
    )

    if not advances:
        return

    # ---------------------------------------------------------
    # Make sure advances table is empty
    # ---------------------------------------------------------

    invoice.set("advances", [])

    remaining_invoice_amount = frappe.utils.flt(
        invoice.grand_total
    )

    # ---------------------------------------------------------
    # Allocate advances
    # ---------------------------------------------------------

    for advance in advances:

        if remaining_invoice_amount <= 0:
            break

        advance_amount = frappe.utils.flt(
            advance.unallocated_amount
        )

        if advance_amount <= 0:
            continue

        allocated_amount = min(
            advance_amount,
            remaining_invoice_amount
        )

        # -----------------------------------------------------
        # Add advance row to Sales Invoice
        # -----------------------------------------------------

        invoice.append("advances", {
            "reference_type": "Payment Entry",
            "reference_name": advance.name,
            "advance_amount": advance_amount,
            "allocated_amount": allocated_amount,
            "ref_exchange_rate": (
                frappe.utils.flt(advance.source_exchange_rate)
                or 1
            ),
            "difference_posting_date": invoice.posting_date,
            "remarks": "Advance allocated from Payment Entry {0}".format(
                advance.name
            ),
        })

        remaining_invoice_amount -= allocated_amount

    # ---------------------------------------------------------
    # Return useful information
    # ---------------------------------------------------------

    return {
        "invoice": invoice.name,
        "invoice_amount": invoice.grand_total,
        "allocated": (
            frappe.utils.flt(invoice.grand_total)
            - remaining_invoice_amount
        ),
        "remaining": remaining_invoice_amount,
    }

@frappe.whitelist()
def make_advance_payment(job_card_name, payment_amount):

    job_card = frappe.get_doc("Revive Job Card", job_card_name)

    if not job_card.customer:
        frappe.throw(_("Customer is required."))

    try:
        payment_amount = frappe.utils.flt(payment_amount)
    except Exception:
        frappe.throw(_("Invalid payment amount."))

    if payment_amount <= 0:
        frappe.throw(_("Payment amount must be greater than zero."))

    # ---------------------------------------------------------
    # Create Payment Entry
    # ---------------------------------------------------------

    payment_entry = frappe.new_doc("Payment Entry")

    payment_entry.payment_type = "Receive"
    payment_entry.party_type = "Customer"
    payment_entry.party = job_card.customer

    # ---------------------------------------------------------
    # Company
    # ---------------------------------------------------------

    payment_entry.company = frappe.defaults.get_global_default("company")

    if not payment_entry.company:
        frappe.throw(_("Please set a default Company."))

    # ---------------------------------------------------------
    # Set Job Card
    # ---------------------------------------------------------

    payment_entry.custom_job_card = job_card.name

    # ---------------------------------------------------------
    # Amount
    # ---------------------------------------------------------

    payment_entry.paid_amount = payment_amount
    payment_entry.received_amount = payment_amount

    # ---------------------------------------------------------
    # Get standard ERPNext party account
    # ---------------------------------------------------------

    from erpnext.accounts.party import get_party_account

    payment_entry.party_account = get_party_account(
        "Customer",
        job_card.customer,
        payment_entry.company
    )

    if not payment_entry.party_account:
        frappe.throw(
            _("No receivable account found for Customer {0}.").format(
                job_card.customer
            )
        )

    # ---------------------------------------------------------
    # Get account currency
    # ---------------------------------------------------------

    payment_entry.party_account_currency = frappe.db.get_value(
        "Account",
        payment_entry.party_account,
        "account_currency"
    )

    # ---------------------------------------------------------
    # No invoice references
    # ---------------------------------------------------------

    payment_entry.set("references", [])

    # ---------------------------------------------------------
    # Populate remaining standard values
    # ---------------------------------------------------------

    payment_entry.set_missing_values()

    # Restore requested amount after set_missing_values()
    payment_entry.paid_amount = payment_amount
    payment_entry.received_amount = payment_amount

    # ---------------------------------------------------------
    # Save as Draft
    # ---------------------------------------------------------


# ---------------------------------------------------------
# Account currencies
# ---------------------------------------------------------

    payment_entry.party_account_currency = frappe.db.get_value(
        "Account",
        payment_entry.party_account,
        "account_currency"
    )

    company_currency = frappe.db.get_value(
        "Company",
        payment_entry.company,
        "default_currency"
    )

    # ---------------------------------------------------------
    # Exchange rates
    # ---------------------------------------------------------

    if payment_entry.party_account_currency == company_currency:
        payment_entry.source_exchange_rate = 1
        payment_entry.target_exchange_rate = 1
    else:
        # Let ERPNext determine the appropriate conversion rate
        payment_entry.source_exchange_rate = 1
        payment_entry.target_exchange_rate = 1
        payment_entry.insert(ignore_permissions=True)
    return payment_entry.name
    # return {
    #     "name": payment_entry.name,
    #     "payment_amount": payment_amount,
    # }