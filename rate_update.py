import frappe

def tax_update()
    item_list = frappe.db.sql("""SELECT item.name as item_name , gst.item_tax_template from `tabItem` item LEFT JOIN `tabItem Tax` gst ON gst.parent = item.name WHERE gst.item_tax_template = 'GST 28% - RM'""", as_dict = 1)
    for item in item_list:
        frappe.db.sql(""" UPDATE `tabItem Price` SET price_list_rate = ( price_list_rate / 128 ) * 100 WHERE item_code = '%(item_code)s'"""%{"item_code":item['item_name']} )
