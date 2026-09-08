# Copyright (c) 2026, MM0 and contributors
# For license information, please see license.txt

# import frappe
from frappe.model.document import Document


class ReviveJobCard(Document):
	def validate(self):
		if not self.is_new():
			previous_status = frappe.db.get_value(
				"Revive Job Card", self.name, "status"
			)

			if previous_status == "Cancelled":
				frappe.throw(_("Cancelled Job Cards cannot be modified."))
