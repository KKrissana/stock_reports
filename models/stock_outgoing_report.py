# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
from odoo import fields, models, _, SUPERUSER_ID
from odoo.exceptions import UserError

from datetime import datetime, time, timedelta
from lxml.html import fromstring

MAX_NAME_LENGTH = 50

class StockOutgoingReportCustomHandle(models.AbstractModel):
    _name = "stock.outgoing.report.handler"
    _inherit = "account.report.custom.handler"
    _description = "Stock Outgoing Report Custom Handler"

    ####################################################
    # INHERIT METHOD
    ####################################################
    def _dynamic_lines_generator(self, report, options, all_column_groups_expression_totals, warnings=None):
        """ Generates lines dynamically for reports that require a custom processing which cannot be handled
        by regular report engines.
        :return:    A list of tuples [(sequence, line_dict), ...], where:
                    - sequence is the sequence to apply when rendering the line (can be mixed with static lines),
                    - line_dict is a dict containing all the line values.
        return []
        """
        lines = []
        

        is_groupby = options.get("stock_grouping_field") != "none"
        company_currency = self.env.company.currency_id
        quantity_digits = self.env['decimal.precision'].precision_get('Product Unit of Measure')
        column_expression = self.env['account.report.expression']

        # add the groups by grouping_field
        group_lines, results = self._get_values(report, options)
        totals= {"quantity": 0.0, "value": 0.0}

        # Report group line (group by account id)
        for group_id in group_lines.keys():
            group_line = group_lines[group_id]
            columns = []
            for column in options["columns"]:
                expr_label = column['expression_label']
                column_value = group_line.get(expr_label, None)

                 # Summary for total line 
                if expr_label in totals.keys() :
                    totals[expr_label] += column_value 

                columns.append(report._build_column_dict(column_value, column, options=options, column_expression=column_expression, currency=company_currency))
            
            line_id = report._get_generic_line_id('account.account', group_id)
            is_in_unfolded_lines = True if not is_groupby else any(report._get_res_id_from_line_id(line_id, 'account.account') == group_id for line_id in options.get('unfolded_lines'))

            lines.append((0, {
                'id': line_id,
                'name': "%s %s" % (group_line.get("group_code") if group_id != 0 else "", group_line.get("group_name")),
                'columns': columns,
                'level': 1,
                'unfoldable': True,
                'unfolded': is_in_unfolded_lines or options.get('unfold_all'),
                'class': 'd-none' if not is_groupby else '',
                'expand_function': '_report_expand_unfoldable_line_stock_outgoing',
            }))

        # Report total line
        columns = []
        for column in options["columns"]:
            expr_label = column['expression_label']
            column_value = totals[expr_label] if expr_label in totals.keys() else None
            columns.append(report._build_column_dict(column_value, column, options=options, currency=company_currency, digits=quantity_digits, column_expression=column_expression))

        lines.append((0, {
            'id': report._get_generic_line_id(None, None, markup='total'),
            'name': _('Total'),
            'level': 1,
            'columns': columns,
        }))
        return lines

    def _caret_options_initializer(self):
        return {
            "stock.reports": [
                {"name": _("Product"), "action": "open_product"},
            ],
            "stock.report.outgoing": [
                {"name": _("Product"), "action": "open_product"},
                {"name": _("Transfer"), "action": "open_picking"},
                {"name": _("Stock Move"), "action": "open_stock_move"}
            ],
            "stock.report.order.outgoing": [
                {"name": _("Product"), "action": "open_product"},
                {"name": _("Transfer"), "action": "open_picking"},
                {"name": _("Stock Move"), "action": "open_stock_move"},
                {"name": _("Order Line"), "action": "open_order_line"}
            ]

        }
    
    def _custom_options_initializer(self, report, options, previous_options):
        """ To be overridden to add report-specific _init_options... code to the report. """
        super()._custom_options_initializer(report, options, previous_options=previous_options)
        # column_group_options_map = report._split_options_per_column_group(options)

        # Options standard account_reports
        options["all_entries"] = None
        options["ignore_totals_below_sections"] = True
        
        # Custom Handler Options
        # if getattr(self.env["stock.move"], "analytic_account_id", False):
        options["stock_grouping"] = "outgoing"
        options['stock_grouping_field'] = previous_options.get("stock_grouping_field") or "none"
        options["stock_valuation_type"] = previous_options.get("stock_valuation_type") or "all"

        options["delivery_stock"] = previous_options.get("delivery_stock", True)
        options["return_from_customer"] = previous_options.get("return_from_customer", False)
        options["return_from_production"] = previous_options.get("return_from_production", False)
        options["return_to_vendor"] = previous_options.get("return_to_vendor", False)
        options["inventory_loss"] = previous_options.get("inventory_loss", False)

        options["show_origin"] = previous_options.get("show_origin", True) 
        options["show_partner"] = previous_options.get("show_partner", False)
        options["show_machine"] = previous_options.get("show_machine", True)
        options["show_analytic_account"] = previous_options.get("show_analytic_account", False) 
        options["show_stock_account"] = previous_options.get("show_stock_account", False) 
        options["show_counterpart_account"] = previous_options.get("show_counterpart_account", False) 

        # set columns
        columns = []
        for column in options['columns']:
            expr_label = column['expression_label']
            if "show_%s" % expr_label in options and not options.get("show_%s" % expr_label):
                continue
            columns.append(column)
        options['columns'] = columns

    ####################################################
    # BUSSINESS METHOD
    ####################################################
    def _get_values(self, report, options, expanded_line_ids=[], offset=0, limit=None):
        # " Get the data from the database "
        self.env['stock.valuation.report'].check_access('read')

        # parameters 
        group_lines = {}
        date_to = options['date']['date_to'] + ' ' + str(time.max.strftime("%H:%M:%S.%f"))
        date_from = options['date']['date_from'] + ' ' + str(time.max.strftime("%H:%M:%S.%f"))
        company_ids = report.get_report_company_ids(options)
        domain = [
            ("stock_move_date", ">=", date_from), 
            ("stock_move_date", "<=", date_to), 
            ("company_id", "in", company_ids),
            ("product_id.is_storable", "=", True),
        ]

        # Options Filter by picking
        selected_pickings = report._get_options_picking(options)
        if selected_pickings:
            domain.append(("stock_move_id.picking_id", "in", selected_pickings))
        
        # Options Filter by product and Categories
        selected_products, selected_product_categories = report._get_options_product(options)
        if selected_products:
            domain.append(("product_id", "in", selected_products))

        if selected_product_categories:
            domain.append(("product_id.categ_id", "in", selected_product_categories))

        # Options Filter by valuation type
        if options["stock_valuation_type"] == "manual_periodic":
            domain.append(("stock_account_id", "=", False))
        elif options["stock_valuation_type"] == "real_time":
            domain.append(("stock_account_id", "!=", False))

        # Options Filter by Analytic and line expanded by analytic
        if getattr(self.env["stock.move"], "analytic_account_id", False):
            if options["analytic_accounts"] \
                or (options["stock_grouping_field"] == "analytic" and expanded_line_ids):
                domain.append(("stock_move_id.analytic_account_id", "in", expanded_line_ids))

        # Options Filter by line expanded by account_id
        if options["stock_grouping_field"] == "account_id" and expanded_line_ids:
            domain.append(("stock_account_id", "in", expanded_line_ids))

        # Options Filter for Outgoing
        if not options.get("delivery_stock") \
            and not options.get("return_from_customer") \
            and not options.get("return_from_production") \
            and not options.get("return_to_vendor") \
            and not options.get("inventory_loss"):
            domain.append(("stock_move_type", "=", "Outgoing"))
        else:
             # Options Filter Receipt to stock 
            usage = []
            if options.get("delivery_stock"):
                usage += ["customer", "production", "equi", "transit"]

            # Options Filter Inventory loss / adjusment
            if options.get("inventory_loss"):
                usage += ["inventory"]

            # Options Filter Return to vendor
            if options.get("return_to_vendor"):
                usage += ["supplier"]

            outgoing_domain = [
                ("stock_move_id.move_line_ids.location_id.usage", "in", ["internal", "transit"]),
                ("stock_move_id.move_line_ids.location_dest_id.usage", "in", usage)
            ]

            # Options Filter Return from production or Return from customer
            if options.get("return_from_production") or options.get("return_from_customer"):
                moves = self.env["stock.valuation.report"].search(domain + outgoing_domain) if usage else self.env["stock.valuation.report"]

                # Return from production
                if options.get("return_from_production"):
                    moves |= self.env["stock.valuation.report"].search(domain + [
                        ("stock_move_id.move_line_ids.location_id.usage", "in", ["production", "equi"]),
                        ("stock_move_id.move_line_ids.location_dest_id.usage", "in", ["internal", "transit"])
                    ])

                # Return from customer
                if options.get("return_from_customer"):
                    moves |= self.env["stock.valuation.report"].search(domain + [
                        ("stock_move_id.move_line_ids.location_id.usage", "in", ["customer"]),
                        ("stock_move_id.move_line_ids.location_dest_id.usage", "in", ["internal", "transit"])
                    ])
                domain = [("id", "in", moves.ids)]
            else:
                domain += outgoing_domain
         
        # search
        results = self.env["stock.valuation.report"].search(domain, offset=offset, limit=limit, order="create_date asc")
        
        # For first load group line
        if not expanded_line_ids: 
            if options["stock_grouping_field"] == "account_id":
                accounts = results.mapped("stock_account_id")
                for account in accounts:
                    res = results.filtered(lambda r: r.stock_account_id == account)
                    group_lines.update(self._get_group_line_dic(account.id, account.code, account.name, res))
                group_lines.update(self._get_group_line_dic(0, "", "Undefined", results.filtered(lambda r: not r.stock_account_id)))
            elif options["stock_grouping_field"] == "analytic": 
                analytics = results.mapped("stock_account_id.analytic_account_id")
                for analytic in analytics:
                    res = results.filtered(lambda r: r.stock_move_id.analytic_account_id == analytic)
                    group_lines.update(self._get_group_line_dic(analytic.id, analytic.code, account.name, res))
                group_lines.update(self._get_group_line_dic(0, "", "Undefined", results.filtered(lambda r: not r.stock_move_id.analytic_account_id)))
            else:
                group_lines.update(self._get_group_line_dic(0, "no_group", "No Grouping", results))
        return group_lines, results

    ####################################################
    # COLUMN/LINE HELPERS
    ####################################################
    def _get_group_line_dic(self, id, code, name, results):
        if results:
            return {
                id: {
                    "group_code": code,
                    "group_name": name,
                    "quantity": sum(results.mapped(lambda r: r.quantity)) * -1, 
                    "value": sum(results.mapped(lambda r: r.value)) * -1
                }
            }
        return {}
    
    def _get_line_dic(self, options, result):
        stock_move = result.stock_move_id and result.stock_move_id.with_user(SUPERUSER_ID) or self.env["stock.move"]
        picking = stock_move and stock_move.picking_id.with_user(SUPERUSER_ID) or self.env["stock.picking"]
        sale_line = stock_move and stock_move.sale_line_id.with_user(SUPERUSER_ID) or self.env["sale.order.line"]
        purchase_line = stock_move and stock_move.purchase_line_id.with_user(SUPERUSER_ID) or self.env["purchase.order.line"]
        requisition = getattr(picking, "requisition_picking_id", False)

        machine = ""
        remark = result.description if not stock_move else ""
        
        # Case has maintenance
        if getattr(stock_move, "maintenance_id", False):
            maintenance = stock_move.maintenance_id.with_user(SUPERUSER_ID)
            if picking and requisition:
                machine = (picking.requisition_picking_id.reason_for_requisition or "").strip()
                if machine != "":
                    if getattr(maintenance, "request_id", False):
                        machine = machine.replace("[%s (%s)] \nEquipment: " % (maintenance.request_id.name, maintenance.name), "").split("\n")[0]
                    else:
                        machine = machine.replace("[%s]  \nEquipment: " % maintenance.name, "").split("\n")[0]
            asset_name = (maintenance.equipment_id.asset_id or "").strip()
            machine = "%s: %s" % (asset_name, machine or maintenance.equipment_id.name)
            remark = maintenance.cause or machine

        # Case has vehicle or requisition
        elif stock_move and picking and (getattr(picking, "requisition_vehicle_id", False) or requisition):
            if requisition:
                requisition = picking.requisition_picking_id.with_user(SUPERUSER_ID)
                requisition_lines = requisition.requisition_line_ids.filtered(lambda r: r.product_id.id == stock_move.product_id.id)
                if getattr(picking, "requisition_vehicle_id", False):
                    vehicle = picking.requisition_vehicle_id.with_user(SUPERUSER_ID)
                    machine = "%s: %s" % (vehicle.asset_ref, vehicle.display_name) if vehicle.asset_ref else vehicle.display_name
                    requisition_lines.filtered(lambda r: r.vehicle_id.id == vehicle.id)
                line_remark = ", ".join(requisition_lines.mapped(lambda r: r.remark or ''))
                remark = line_remark or requisition.reason_for_requisition or ""

        line = {
            "id": result.id,
            "date": result.stock_move_date,
            "product_id": result.product_id.id,
            "product_code": result.product_id.default_code,
            "product_name": result.product_id.name,
            "picking_id": picking and picking.id or False,
            "move_id": stock_move and stock_move.id or False,
            "sale_line_id": sale_line and sale_line.id or False,
            "purchase_line_id": purchase_line and purchase_line.id or False,
            "reference": result.stock_move_id and result.stock_move_id.reference or "Valuation",
            "quantity": result.quantity * -1,
            "unit_cost": result.unit_cost,
            "value": result.value * -1,
            "uom": result.uom_id.name,
            "machine": machine,
            "remark": remark,
        }

        # Options Show Source 
        if options.get("show_origin"):
            origin = stock_move and stock_move.origin or ""
            if result.quantity > 0 and stock_move.origin_returned_move_id:
                origin = "Return of %s" % stock_move.origin_returned_move_id.reference
                if stock_move.origin_returned_move_id.sale_line_id:
                    origin = "%s / %s" % (origin, stock_move.origin_returned_move_id.sale_line_id.order_id.name) 
                elif stock_move.origin_returned_move_id.purchase_line_id:
                     origin = "%s / %s" % (origin, stock_move.origin_returned_move_id.purchase_line_id.order_id.name)
            elif purchase_line and getattr(purchase_line, "purchase_request_lines", False):
                pr_lines = purchase_line.purchase_request_lines.with_user(SUPERUSER_ID)
                origin += ' / %s' % (', '.join(pr_lines.mapped('request_id.name')),)
            line.update({"origin": origin})
        
        # Options Show Partner
        if options.get("show_partner"):
            partner = picking and picking.partner_id.name or ""
            if not partner and picking and getattr(picking, "requisition_picking_id", False):
                partner = picking.requisition_picking_id.display_name or picking.user_id.display_name or ""
            if not partner and sale_line:
                partner = sale_line.order_partner_id.name or ""
            if not partner and purchase_line:
                partner = purchase_line.partner_id.name or ""
            line.update({"partner": partner})

        # Options Show Analytic Account
        if options.get("show_analytic_account"):
            analytic_account = ""
            if getattr(stock_move, "analytic_account_id", False):
                analytic_account = stock_move.analytic_account_id and stock_move.analytic_account_id.display_name 
            line.update({"analytic_account": analytic_account})

        # Options Show Stock Account
        if options.get("show_stock_account"):
            line.update({"stock_account": result.stock_account_id and result.stock_account_id.display_name or ""})

        # Options Show Counterpart Account
        if options.get("show_counterpart_account"):
            counterpart_account = ""
            if result.account_move_id:
                move_line = result.account_move_id.line_ids.filtered(lambda l: l.product_id.id == result.product_id.id and l.debit > 0)[0]
                counterpart_account = move_line.account_id.display_name
            line.update({"counterpart_account": counterpart_account})
        return line
    
    def _get_report_line (self, report, options, parent_line_id, line_dic):
        company_currency = self.env.company.currency_id
        quantity_digits = self.env['decimal.precision'].precision_get('Product Unit of Measure')

        columns = []
        name = line_dic["product_name"] if options['export_mode'] == 'file' else "[%s] %s" % (line_dic["product_code"], line_dic["product_name"])

        for column in options["columns"]:
            # column_group_key = column['column_group_key']
            expr_label = column['expression_label']
            column_value = line_dic.get(expr_label, None)
            columns.append(report._build_column_dict(column_value, column, options=options, currency=company_currency, digits=quantity_digits))

        # Report detail line
        caret_options = "stock.reports"
        markup = {"stock.valuation.report": line_dic["id"]}
        if line_dic["picking_id"]:
            markup.update({"stock.picking": line_dic["picking_id"]})
        if line_dic["move_id"]:
            markup.update({"stock.move": line_dic["move_id"]})
            caret_options = "stock.report.outgoing"
        if line_dic["sale_line_id"]:
            markup.update({"sale.order.line": line_dic["sale_line_id"]})
            caret_options = "stock.report.order.outgoing"
        if line_dic["purchase_line_id"]:
            markup.update({"purchase.order.line": line_dic["purchase_line_id"]})
            caret_options = "stock.report.order.outgoing"
        line = {
            'id': report._get_generic_line_id('product.product', line_dic["product_id"], parent_line_id=parent_line_id, markup=markup),
            'level': 2,
            'code': line_dic["product_code"],
            'name': name,
            'columns': columns,
            'parent_id': parent_line_id,
            'unfoldable': False,
            'unfolded': False,
            'caret_options': caret_options
        }
        # set title
        if len(name) > MAX_NAME_LENGTH:
            line.update({'title_hover': name})
        return line

    def _report_expand_unfoldable_line_stock_outgoing(self, line_dict_id, groupby, options, progress, offset, unfold_all_batch_data=None):
        lines = []
        report = self.env['account.report'].browse(options['report_id'])

        model, line_id = report._get_model_info_from_id(line_dict_id)
        expanded_line_ids = []

        if model != 'account.account':
            raise UserError(_("Wrong ID for report line to expand: %s", line_dict_id))
        
        if options.get("stock_grouping_field") != "none":
            expanded_line_ids = [False if line_id == 0 else line_id]

        # get value line
        line_counter = 0
        limit_to_load = report.load_more_limit + 1 if report.load_more_limit and options['export_mode'] != 'print' else None
        group_lines, results = self._get_values(report, options, expanded_line_ids, offset=offset, limit=limit_to_load)
        has_more = False
        for res in results:
            line_counter += 1
            if line_counter == limit_to_load:
                has_more = True
                break
            lines.append(self._get_report_line(report, options, line_dict_id, self._get_line_dic(options, res)))

        return {
            'lines': lines,
            'offset_increment': report.load_more_limit,
            'has_more': has_more,
            #'progress': next_progress,
        }