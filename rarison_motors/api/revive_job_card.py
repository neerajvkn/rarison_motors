import frappe
from frappe import _
from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry
from frappe.utils import flt

# SOURCE_WAREHOUSE = "Stores - R"
# TARGET_WAREHOUSE = "Goods In Transit - R"
PRICE_LIST = "Standard Selling"

settings = frappe.get_single("Revive Settings")
SOURCE_WAREHOUSE = settings.store_warehouse
TARGET_WAREHOUSE = settings.items_issued_warehouse


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

            # spare_invoice.append("items", {
            #     "item_code": row.item,
            #     "item_name": item.item_name,
            #     "qty": row.qty or 1,
            #     "rate": row.rate or 0,
            #     "warehouse": row.warehouse if hasattr(row, "warehouse") else None
            # })
            spare_invoice.append("items", {
                "item_code": row.item,
                "item_name": item.item_name,
                "qty": row.qty or 1,
                "uom": row.uom or item.stock_uom,
                "conversion_factor": (
                    frappe.utils.get_uom_conv_factor(row.item, row.uom)
                    if row.uom and row.uom != item.stock_uom
                    else 1
                ),
                "rate": row.rate or 0,
                "warehouse": TARGET_WAREHOUSE or None
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

@frappe.whitelist()
def issue_stock(parent_doctype, parent_name, items):
    """
    Transfer stock from SOURCE_WAREHOUSE to TARGET_WAREHOUSE
    and add the transferred items to the parent's spares_used table.

    Expected items:
    [
        {
            "item": "ITEM-001",
            "qty": 2
        },
        {
            "item": "ITEM-002",
            "qty": 1
        }
    ]
    """

    if isinstance(items, str):
        items = frappe.parse_json(items)

    if not items:
        frappe.throw(_("No items were selected."))

    # ------------------------------------------------------------------
    # Validate parent
    # ------------------------------------------------------------------

    parent = frappe.get_doc(parent_doctype, parent_name)

    if parent.doctype != "Revive Job Card":
        frappe.throw(_("Invalid parent document."))

    # ------------------------------------------------------------------
    # Validate warehouses
    # ------------------------------------------------------------------

    for warehouse in [SOURCE_WAREHOUSE, TARGET_WAREHOUSE]:
        if not frappe.db.exists("Warehouse", warehouse):
            frappe.throw(
                _("Warehouse {0} does not exist.").format(
                    frappe.bold(warehouse)
                )
            )

    if SOURCE_WAREHOUSE == TARGET_WAREHOUSE:
        frappe.throw(_("Source and target warehouses cannot be the same."))

    # ------------------------------------------------------------------
    # Clean and combine items
    #
    # Combine by (item_code, uom) so we don't merge quantities that are
    # actually in different units. Also track the stock-UOM-equivalent
    # quantity for each item so we can validate against Bin qty, which
    # is always in stock UOM.
    # ------------------------------------------------------------------

    combined_items = {}       # key: (item_code, uom) -> {"qty": .., "conversion_factor": ..}
    stock_qty_by_item = {}    # key: item_code -> total qty in stock UOM (for validation)

    for row in items:
        item_code = row.get("item")
        qty = flt(row.get("qty"))
        uom = row.get("uom")
        conversion_factor = flt(row.get("conversion_factor")) or 1

        if not item_code:
            frappe.throw(_("Item Code is required."))

        if qty <= 0:
            frappe.throw(
                _("Quantity for item {0} must be greater than zero.").format(
                    item_code
                )
            )

        if not uom:
            frappe.throw(
                _("UOM is required for item {0}.").format(item_code)
            )

        if not frappe.db.exists("Item", item_code):
            frappe.throw(
                _("Item {0} does not exist.").format(
                    item_code
                )
            )

        key = (item_code, uom)

        if key in combined_items:
            combined_items[key]["qty"] += qty
        else:
            combined_items[key] = {
                "qty": qty,
                "conversion_factor": conversion_factor,
            }

        stock_qty_by_item[item_code] = (
            stock_qty_by_item.get(item_code, 0)
            + (qty * conversion_factor)
        )

    # ------------------------------------------------------------------
    # Validate available stock (compare stock-UOM-equivalent totals)
    # ------------------------------------------------------------------

    for item_code, required_stock_qty in stock_qty_by_item.items():

        stock_qty = flt(
            frappe.db.get_value(
                "Bin",
                {
                    "item_code": item_code,
                    "warehouse": SOURCE_WAREHOUSE,
                },
                "actual_qty",
            )
        )

        if required_stock_qty > stock_qty + 0.000001:
            frappe.throw(
                _(
                    "Insufficient stock for {0}. "
                    "Available: {1}, Requested: {2}"
                ).format(
                    frappe.bold(item_code),
                    stock_qty,
                    required_stock_qty,
                )
            )

    validated_items = []

    for (item_code, uom), data in combined_items.items():

        qty = data["qty"]
        conversion_factor = data["conversion_factor"]

        item_name = frappe.db.get_value(
            "Item",
            item_code,
            "item_name",
        )

        # --------------------------------------------------------------
        # Get Standard Selling price
        # --------------------------------------------------------------

        rate = frappe.db.get_value(
            "Item Price",
            {
                "item_code": item_code,
                "price_list": PRICE_LIST,
                "selling": 1,
                "uom": uom,
                "currency": frappe.db.get_default("currency"),
            },
            "price_list_rate",
            order_by="valid_from desc",
        )

        # If no price was found, try without currency restriction.
        if rate is None:
            rate = frappe.db.get_value(
                "Item Price",
                {
                    "item_code": item_code,
                    "price_list": PRICE_LIST,
                    "selling": 1,
                    "uom": uom,
                },
                "price_list_rate",
                order_by="valid_from desc",
            )

        rate = flt(rate)

        validated_items.append(
            {
                "item": item_code,
                "item_name": item_name,
                "qty": qty,
                "uom": uom,
                "conversion_factor": conversion_factor,
                "rate": rate,
                "total": rate * qty,
            }
        )

    # ------------------------------------------------------------------
    # Create Material Transfer
    # ------------------------------------------------------------------

    stock_entry = frappe.new_doc("Stock Entry")

    stock_entry.stock_entry_type = "Material Transfer"

    stock_entry.from_warehouse = SOURCE_WAREHOUSE
    stock_entry.to_warehouse = TARGET_WAREHOUSE

    stock_entry.remarks = _(
        "Stock issued to workshop from Revive Job Card {0}"
    ).format(parent.name)

    for row in validated_items:

        # stock_entry.append(
        #     "items",
        #     {
        #         "item_code": row["item"],
        #         "qty": row["qty"],
        #         "s_warehouse": SOURCE_WAREHOUSE,
        #         "t_warehouse": TARGET_WAREHOUSE,
        #     },
        # )

        stock_entry.append(
            "items",
            {
                "item_code": row["item"],
                "qty": row["qty"],
                "uom": row["uom"],
                "conversion_factor": row["conversion_factor"],
                "s_warehouse": SOURCE_WAREHOUSE,
                "t_warehouse": TARGET_WAREHOUSE,
            },
        )

    # Insert + submit inside the current transaction.
    stock_entry.insert(ignore_permissions=True)
    stock_entry.submit()

    # ------------------------------------------------------------------
    # Add rows to spares_used
    # ------------------------------------------------------------------

    for row in validated_items:

        # child = parent.append(
        #     "spares_used",
        #     {
        #         "item": row["item"],
        #         "name1": row["item_name"],
        #         "rate": row["rate"],
        #         "qty": row["qty"],
        #         "total": row["total"],
        #     },
        # )

        # parent.append(
        #     "spares_used",
        #     {
        #         "item": row["item"],
        #         "name1": row["item_name"],
        #         "qty": row["qty"],
        #         "uom": row["uom"],
        #         "rate": row["rate"],
        #         "total": row["total"],
        #     },
        # )
        parent.append(
            "spares_used",
            {
                "item": row["item"],
                "name1": row["item_name"],
                "qty": row["qty"],
                "uom": row["uom"],
                "conversion_factor": row["conversion_factor"],
                "rate": row["rate"],
                "total": row["total"],
            },
        )

    # ------------------------------------------------------------------
    # Save parent
    # ------------------------------------------------------------------

    parent.save(ignore_permissions=True)

    return {
        "success": True,
        "stock_entry": stock_entry.name,
        "items": validated_items,
    }

@frappe.whitelist()
def get_item_details(item_code):

    if not item_code:
        return {}

    if not frappe.db.exists("Item", item_code):
        frappe.throw(_("Item {0} does not exist.").format(item_code))

    available_qty = flt(
        frappe.db.get_value(
            "Bin",
            {
                "item_code": item_code,
                "warehouse": SOURCE_WAREHOUSE,
            },
            "actual_qty",
        )
    )

    rate = frappe.db.get_value(
        "Item Price",
        {
            "item_code": item_code,
            "price_list": PRICE_LIST,
            "selling": 1,
        },
        "price_list_rate",
        order_by="valid_from desc",
    )

    stock_uom = frappe.db.get_value(
        "Item",
        item_code,
        "stock_uom"
    )

    # return {
    #     "item_code": item_code,
    #     "item_name": frappe.db.get_value(
    #         "Item",
    #         item_code,
    #         "item_name"
    #     ),
    #     "available_qty": available_qty,
    #     "rate": flt(rate),
    # }
    return {
        "item_code": item_code,
        "item_name": frappe.db.get_value(
            "Item",
            item_code,
            "item_name"
        ),
        "stock_uom": stock_uom,
        "available_qty": available_qty,
        "rate": flt(rate),
    }

@frappe.whitelist()
def get_item_uoms(doctype, txt, searchfield, start, page_len, filters):

    item_code = filters.get("item_code")

    if not item_code:
        return []

    stock_uom = frappe.db.get_value(
        "Item",
        item_code,
        "stock_uom"
    )

    uoms = frappe.db.sql("""
        SELECT uom
        FROM `tabUOM Conversion Detail`
        WHERE parent = %s
        AND parenttype = 'Item'
        AND uom LIKE %s

        UNION

        SELECT %s

        ORDER BY uom
        LIMIT %s
    """, (
        item_code,
        "%" + txt + "%",
        stock_uom,
        page_len
    ))

    return uoms

@frappe.whitelist()
def get_uom_details(item_code, uom):

    if not item_code or not uom:
        return {}

    stock_uom = frappe.db.get_value(
        "Item",
        item_code,
        "stock_uom"
    )

    if uom == stock_uom:
        conversion_factor = 1
    else:
        conversion_factor = frappe.db.get_value(
            "UOM Conversion Detail",
            {
                "parent": item_code,
                "parenttype": "Item",
                "uom": uom
            },
            "conversion_factor"
        )

    if not conversion_factor:
        frappe.throw(
            _("No conversion factor found for {0} → {1}").format(
                item_code,
                uom
            )
        )

    return {
        "uom": uom,
        "stock_uom": stock_uom,
        "conversion_factor": conversion_factor
    }

@frappe.whitelist()
def get_item_price(item_code, uom):

    if not item_code or not uom:
        return {
            "rate": 0
        }

    rate = frappe.db.get_value(
        "Item Price",
        {
            "item_code": item_code,
            "price_list": "Standard Selling",
            "selling": 1,
            "uom": uom
        },
        "price_list_rate",
        order_by="valid_from desc"
    )

    return {
        "rate": flt(rate)
    }

@frappe.whitelist()
def get_returnable_spares(job_card_name):
    """
    Return the current spares_used rows for a Job Card, so the user
    can pick which ones (and how much) to return to stores.
    """

    job_card = frappe.get_doc("Revive Job Card", job_card_name)

    rows = []

    for row in job_card.get("spares_used") or []:
        rows.append({
            "name": row.name,
            "item": row.item,
            "item_name": row.name1,
            "qty": flt(row.qty),
            "uom": row.uom,
            "conversion_factor": flt(row.conversion_factor) or 1,
            "rate": flt(row.rate),
        })

    return rows


def _create_return_stock_entry(parent, rows, remarks):
    """
    Shared helper: create + submit a Material Transfer Stock Entry
    moving `rows` (item/qty/uom/conversion_factor) from
    TARGET_WAREHOUSE back to SOURCE_WAREHOUSE. Mirrors issue_stock's
    own Stock Entry logic, just reversed.

    `rows` is a list of dicts with keys: item, qty, uom, conversion_factor.

    Returns the submitted Stock Entry name, or None if `rows` is empty.
    """

    if not rows:
        return None

    for warehouse in [SOURCE_WAREHOUSE, TARGET_WAREHOUSE]:
        if not frappe.db.exists("Warehouse", warehouse):
            frappe.throw(
                _("Warehouse {0} does not exist.").format(
                    frappe.bold(warehouse)
                )
            )

    # ------------------------------------------------------------
    # Validate that enough stock exists in the Issued warehouse,
    # in stock-UOM-equivalent terms.
    # ------------------------------------------------------------

    stock_qty_by_item = {}

    for row in rows:
        conversion_factor = flt(row.get("conversion_factor")) or 1
        stock_qty_by_item[row["item"]] = (
            stock_qty_by_item.get(row["item"], 0)
            + flt(row["qty"]) * conversion_factor
        )

    for item_code, required_stock_qty in stock_qty_by_item.items():

        available = flt(
            frappe.db.get_value(
                "Bin",
                {
                    "item_code": item_code,
                    "warehouse": TARGET_WAREHOUSE,
                },
                "actual_qty",
            )
        )

        if required_stock_qty > available + 0.000001:
            frappe.throw(
                _(
                    "Cannot return {0} of {1}. Only {2} is currently "
                    "available in {3}."
                ).format(
                    required_stock_qty,
                    frappe.bold(item_code),
                    available,
                    frappe.bold(TARGET_WAREHOUSE),
                )
            )

    stock_entry = frappe.new_doc("Stock Entry")
    stock_entry.stock_entry_type = "Material Transfer"
    stock_entry.from_warehouse = TARGET_WAREHOUSE
    stock_entry.to_warehouse = SOURCE_WAREHOUSE
    stock_entry.remarks = remarks

    for row in rows:
        stock_entry.append("items", {
            "item_code": row["item"],
            "qty": flt(row["qty"]),
            "uom": row.get("uom"),
            "conversion_factor": flt(row.get("conversion_factor")) or 1,
            "s_warehouse": TARGET_WAREHOUSE,
            "t_warehouse": SOURCE_WAREHOUSE,
        })

    stock_entry.insert(ignore_permissions=True)
    stock_entry.submit()

    return stock_entry.name


@frappe.whitelist()
def return_stock(parent_doctype, parent_name, items):
    """
    Return specific quantities of already-issued spares back to
    SOURCE_WAREHOUSE, via a reverse Material Transfer.

    Expected items:
    [
        {
            "row_name": "<spares_used child row name>",
            "item": "ITEM-001",
            "qty": 1,
            "uom": "Nos",
            "conversion_factor": 1
        }
    ]
    """

    if isinstance(items, str):
        items = frappe.parse_json(items)

    if not items:
        frappe.throw(_("No items were selected for return."))

    parent = frappe.get_doc(parent_doctype, parent_name)

    if parent.doctype != "Revive Job Card":
        frappe.throw(_("Invalid parent document."))

    if parent.status == "Cancelled":
        frappe.throw(_("This Job Card is already cancelled."))

    spares_by_name = {
        row.name: row for row in (parent.get("spares_used") or [])
    }

    return_rows = []
    summary_lines = []
    rows_to_remove = []

    for entry in items:

        row_name = entry.get("row_name")
        qty = flt(entry.get("qty"))

        row = spares_by_name.get(row_name)

        if not row:
            frappe.throw(
                _("Spares row {0} was not found on this Job Card.").format(
                    row_name
                )
            )

        if qty <= 0:
            frappe.throw(
                _("Return quantity for {0} must be greater than zero.").format(
                    row.item
                )
            )

        if qty > flt(row.qty) + 0.000001:
            frappe.throw(
                _(
                    "Cannot return {0} of {1}. Only {2} {3} is issued "
                    "on this Job Card."
                ).format(
                    qty,
                    frappe.bold(row.item),
                    flt(row.qty),
                    row.uom,
                )
            )

        conversion_factor = flt(row.conversion_factor) or 1

        return_rows.append({
            "item": row.item,
            "qty": qty,
            "uom": row.uom,
            "conversion_factor": conversion_factor,
        })

        summary_lines.append("{0}: {1} {2}".format(row.item, qty, row.uom))

        remaining_qty = flt(row.qty) - qty

        if remaining_qty <= 0.000001:
            rows_to_remove.append(row.name)
        else:
            row.qty = remaining_qty
            row.total = remaining_qty * flt(row.rate)

    stock_entry_name = _create_return_stock_entry(
        parent,
        return_rows,
        _("Stock returned from Issued to Workshop for Revive Job Card {0}")
        .format(parent.name),
    )

    if rows_to_remove:
        parent.set(
            "spares_used",
            [
                row for row in parent.get("spares_used")
                if row.name not in rows_to_remove
            ],
        )

    parent.save(ignore_permissions=True)

    parent.add_comment(
        "Comment",
        _(
            "The following spares have been returned from the issued "
            "list to {0}: {1}"
        ).format(SOURCE_WAREHOUSE, "; ".join(summary_lines)),
    )

    frappe.db.commit()

    return {
        "success": True,
        "stock_entry": stock_entry_name,
    }


@frappe.whitelist()
def cancel_job_card(job_card_name, reason):
    """
    Cancel a Job Card:
      - Log the cancellation reason as a comment.
      - Return all currently-issued spares back to SOURCE_WAREHOUSE.
      - Clear the spares_used table.
      - Set status to Cancelled.
    """

    if not reason or not reason.strip():
        frappe.throw(_("Please provide a cancellation reason."))

    job_card = frappe.get_doc("Revive Job Card", job_card_name)

    if job_card.status == "Cancelled":
        frappe.throw(_("This Job Card is already cancelled."))

    if job_card.spares_invoice or job_card.service_invoice:
        frappe.throw(
            _(
                "This Job Card already has Sales Invoice(s) linked ({0}). "
                "Please cancel/credit those invoices before cancelling "
                "the Job Card."
            ).format(
                ", ".join(
                    filter(None, [job_card.spares_invoice, job_card.service_invoice])
                )
            )
        )

    spares = job_card.get("spares_used") or []

    return_rows = [
        {
            "item": row.item,
            "qty": flt(row.qty),
            "uom": row.uom,
            "conversion_factor": flt(row.conversion_factor) or 1,
        }
        for row in spares
    ]

    summary_lines = [
        "{0}: {1} {2}".format(row["item"], row["qty"], row["uom"])
        for row in return_rows
    ]

    stock_entry_name = _create_return_stock_entry(
        job_card,
        return_rows,
        _("Stock returned to Stores due to cancellation of Revive Job Card {0}")
        .format(job_card.name),
    )

    job_card.set("spares_used", [])
    job_card.status = "Cancelled"

    job_card.save(ignore_permissions=True)

    job_card.add_comment(
        "Comment",
        _("Job Card cancelled. Reason: {0}").format(reason),
    )

    if summary_lines:
        job_card.add_comment(
            "Comment",
            _(
                "The following spares have been returned from the issued "
                "list to {0}: {1}"
            ).format(SOURCE_WAREHOUSE, "; ".join(summary_lines)),
        )

    frappe.db.commit()

    return {
        "success": True,
        "stock_entry": stock_entry_name,
    }

