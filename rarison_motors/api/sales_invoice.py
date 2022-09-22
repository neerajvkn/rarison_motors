import frappe

@frappe.whitelist()
def sales_inv_api(sinv):
    return frappe.get_doc('Sales Invoice', sinv)