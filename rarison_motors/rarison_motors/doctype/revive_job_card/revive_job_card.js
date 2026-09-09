frappe.ui.form.on("Revive Job Card", {

    refresh(frm) {

        if (frm.is_new()) {
            return;
        }

        if (frm.doc.status === "Cancelled") {
            frm.disable_form();
            return;
        }

        // frm.add_custom_button(__("Issue Stock"), () => {
        //     open_issue_stock_dialog(frm);
        // });

        if ((frm.doc.spares_used || []).length) {
            frm.add_custom_button(__("Return Stock"), () => {
                open_return_stock_dialog(frm);
            }, __("Actions"));
        }

        frm.add_custom_button(__("Cancel Job Card"), () => {
            open_cancel_job_card_dialog(frm);
        }, __("Actions"));
    }
});


function open_cancel_job_card_dialog(frm) {

    const dialog = new frappe.ui.Dialog({

        title: __("Cancel Job Card"),

        fields: [
            {
                fieldname: "reason",
                label: __("Cancellation Reason"),
                fieldtype: "Small Text",
                reqd: 1
            }
        ],

        primary_action_label: __("Cancel Job Card"),

        primary_action(values) {

            frappe.confirm(
                __(
                    "This will return all issued spares to Stores and permanently lock this Job Card. Are you sure?"
                ),

                () => {

                    frappe.call({

                        method: "rarison_motors.api.revive_job_card.cancel_job_card",

                        freeze: true,
                        freeze_message: __("Cancelling job card..."),

                        args: {
                            job_card_name: frm.docname,
                            reason: values.reason
                        },

                        callback(response) {

                            if (!response.message) {
                                return;
                            }

                            frappe.show_alert({
                                message: __("Job Card cancelled successfully."),
                                indicator: "green"
                            });

                            dialog.hide();

                            frm.reload_doc();
                        }
                    });
                }
            );
        }
    });

    dialog.show();
}


function open_return_stock_dialog(frm) {

    const dialog = new frappe.ui.Dialog({

        title: __("Return Stock"),

        size: "large",

        fields: [
            {
                fieldname: "return_items_html",
                fieldtype: "HTML"
            }
        ],

        primary_action_label: __("Return Stock"),

        primary_action() {

            const items = collect_return_items(dialog);

            if (!items.length) {
                frappe.msgprint({
                    title: __("No Items"),
                    message: __("Please enter a return quantity for at least one part."),
                    indicator: "orange"
                });

                return;
            }

            frappe.confirm(
                __("Are you sure you want to return the selected quantities to Stores?"),

                () => {

                    frappe.call({

                        method: "rarison_motors.api.revive_job_card.return_stock",

                        freeze: true,
                        freeze_message: __("Returning stock..."),

                        args: {
                            parent_doctype: frm.doctype,
                            parent_name: frm.docname,
                            items: items
                        },

                        callback(response) {

                            if (!response.message) {
                                return;
                            }

                            frappe.show_alert({
                                message: __(
                                    "Stock returned successfully. Stock Entry: {0}"
                                ).replace("{0}", response.message.stock_entry),
                                indicator: "green"
                            });

                            dialog.hide();

                            frm.reload_doc();
                        }
                    });
                }
            );
        }
    });

    dialog.show();

    frappe.call({

        method: "rarison_motors.api.revive_job_card.get_returnable_spares",

        args: {
            job_card_name: frm.docname
        },

        callback(response) {
            render_return_items_table(dialog, response.message || []);
        }
    });
}


function render_return_items_table(dialog, rows) {

    let html = `
        <div style="margin-top: 10px;">
            <table class="table table-bordered">
                <thead>
                    <tr>
                        <th>Part No</th>
                        <th>Issued Qty</th>
                        <th>UOM</th>
                        <th style="width:20%">Return Qty</th>
                    </tr>
                </thead>
                <tbody>
    `;

    rows.forEach(row => {

        html += `
            <tr data-row-name="${frappe.utils.escape_html(row.name)}"
                data-item="${frappe.utils.escape_html(row.item)}"
                data-uom="${frappe.utils.escape_html(row.uom || "")}"
                data-conversion-factor="${flt(row.conversion_factor) || 1}"
                data-max-qty="${flt(row.qty)}">

                <td>
                    <b>${frappe.utils.escape_html(row.item)}</b>
                    <br>
                    <small>${frappe.utils.escape_html(row.item_name || "")}</small>
                </td>

                <td>${flt(row.qty)}</td>
                <td>${frappe.utils.escape_html(row.uom || "")}</td>

                <td>
                    <input type="number" class="form-control return-qty-input"
                        min="0" max="${flt(row.qty)}" step="any" value="0">
                </td>
            </tr>
        `;
    });

    if (!rows.length) {
        html += `
            <tr>
                <td colspan="4" class="text-center text-muted">
                    No spares are currently issued on this Job Card.
                </td>
            </tr>
        `;
    }

    html += `</tbody></table></div>`;

    dialog.fields_dict.return_items_html.$wrapper.html(html);
}


function collect_return_items(dialog) {

    const items = [];
    let error_message = null;

    dialog.fields_dict.return_items_html.$wrapper
        .find("tr[data-row-name]")
        .each(function () {

            if (error_message) {
                return false;
            }

            const $row = $(this);
            const qty = flt($row.find(".return-qty-input").val());
            const max_qty = flt($row.attr("data-max-qty"));

            if (qty <= 0) {
                return;
            }

            if (qty > max_qty + 0.000001) {
                error_message = __(
                    "Return quantity for {0} cannot exceed the issued quantity of {1}."
                )
                .replace("{0}", $row.attr("data-item"))
                .replace("{1}", max_qty);

                return false;
            }

            items.push({
                row_name: $row.attr("data-row-name"),
                item: $row.attr("data-item"),
                qty: qty,
                uom: $row.attr("data-uom"),
                conversion_factor: flt($row.attr("data-conversion-factor")) || 1
            });
        });

    if (error_message) {
        frappe.msgprint({
            title: __("Invalid Quantity"),
            message: error_message,
            indicator: "red"
        });
        return [];
    }

    return items;
}