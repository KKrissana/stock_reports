# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
from odoo import models, _, SUPERUSER_ID
from odoo.exceptions import UserError

from datetime import time

MAX_NAME_LENGTH = 40

class StockMoveReportCustomHandle(models.AbstractModel):
    _name = "stock.move.report.handler"
    _inherit = "account.report.custom.handler"
    _description = "Stock Move Report Custom Handler"

    ####################################################
    # INHERIT METHOD
    ####################################################
    def _get_custom_display_config(self):
        return {
            "templates": {
                "AccountReportLineName": "stock_reports.StockReportLineName",
                "AccountReportLineCell": "stock_reports.StockReportLineCell",
            },
        }
    
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
                'expand_function': '_report_expand_unfoldable_line_stock_move_report',
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
            'level': 0,
            'columns': columns,
        }))

        return lines

    def _caret_options_initializer(self):
        return {
            "stock.reports": [
                {"name": _("Product"), "action": "open_product"},
            ],
            "stock.move.report": [
                {"name": _("Product"), "action": "open_product"},
                {"name": _("Transfer"), "action": "open_picking"},
                {"name": _("Stock Move"), "action": "open_stock_move"}
            ],
            "stock.move.report.order.line": [
                {"name": _("Product"), "action": "open_product"},
                {"name": _("Transfer"), "action": "open_picking"},
                {"name": _("Stock Move"), "action": "open_stock_move"},
                {"name": _("Order Line"), "action": "open_order_line"}
            ]
        }

    def _custom_options_initializer(self, report, options, previous_options):
        """ To be overridden to add report-specific _init_options... code to the report. """
        super()._custom_options_initializer(report, options, previous_options=previous_options)
        
        # Options standard account_reports
        options["all_entries"] = None
        options["ignore_totals_below_sections"] = True

        # Custom filters for both incoming and outgoing report
        options['stock_grouping_field'] = previous_options.get("stock_grouping_field") or "none"
        options["stock_valuation_type"] = previous_options.get("stock_valuation_type") or "all"

        # custom options filters for both incoming and outgoing report
        options["track_inventory"] = previous_options.get("track_inventory", True)
        options["none_track_inventory"] = previous_options.get("none_track_inventory", False)
        # ----------------------------------------------------------------#
        options["vendor_return"] = previous_options.get("vendor_return", False)
        options["customer_return"] = previous_options.get("customer_return", False)
        options["mrp_operation_return"] = previous_options.get("mrp_operation_return", False)
        options["inventory_loss"] = previous_options.get("inventory_loss", False)

        # custom options show columns for both incoming and outgoing report
        options["show_origin"] = previous_options.get("show_origin", False) 
        options["show_partner"] = previous_options.get("show_partner", True)
        options["show_analytic_account"] = previous_options.get("show_analytic_account", False) 
        options["show_stock_account"] = previous_options.get("show_stock_account", False) 
        options["show_counterpart_account"] = previous_options.get("show_counterpart_account", False)

        # set columns
        columns = []
        for column in options['columns']:
            expr_label = column['expression_label']
            # condition to show/hide column based on options
            if "show_%s" % expr_label in options and not options.get("show_%s" % expr_label):
                continue
            columns.append(column)
        options['columns'] = columns

    ####################################################
    # BUSSINESS METHOD
    ####################################################
    def _get_values(self, report, options, expanded_line_ids=[], offset=0, limit=None) -> tuple:
        """ This method is used to get the values to display in the report and group them by the grouping field.
        :return:    A tuple (group_lines, results), where:
                    - group_lines is a dict {group_id: {group_code: code, group_name: name, expr_label: value, ...}, ...}
                    - results is a recordset of stock.valuation.report that will be used to display the lines of the report.
        """
        return {}, self.env['stock.valuation.report']

    ####################################################
    # COLUMN/LINE HELPERS
    ####################################################
    def _build_line(self, report, options, parent_line_id, result, company_currency, quantity_digits) -> dict:
        """ This method is used to build the values of the lines to display in the report.
        :param report: The report object -> account.reports
        :param options: The options of the report
        :param parent_line_id: The id of the parent line (used for grouping)
        :param result: The stock.valuation.report record that contains the values to display in the line
        :return: A dict containing the values of the line to display.
        """
        columns = []
        line_dic = self._build_line_dic(options, result)
    
        line_column_max = self._get_column_max_characters()
        for column in options["columns"]:
            expr_label = column['expression_label']
            column_value = line_dic.get(expr_label, None)
            line_column = report._build_column_dict(column_value, column, options=options, currency=company_currency, digits=quantity_digits)
            if expr_label in line_column_max.keys():
                line_column.update({"maxCharacters": line_column_max[expr_label]})
            columns.append(line_column)

        # Report detail line
        name = line_dic.get("name", "")
        markup, caret_options = self._build_line_markup(line_dic)
        line = {
            'id': report._get_generic_line_id('product.product', line_dic["product_id"], parent_line_id=parent_line_id, markup=markup),
            'level': 2,
            'code': line_dic["product_code"],
            'name': name,
            'columns': columns,
            'parent_id': parent_line_id,
            'unfoldable': False,
            'unfolded': False,
            'caret_options': caret_options,
            'maxCharacters': MAX_NAME_LENGTH,
        }
        # set title
        if len(name) > MAX_NAME_LENGTH:
            line.update({'title_hover': name})
        return line

    def _build_line_dic(self, options, result) -> dict:
        """ This method is used to build the line dict for the given result.
        :param options: The options of the report
        :param result: The stock.valuation.report record that contains the values to display in the line
        :return: A dict containing the values of the line to display.
        """
        stock_move = result.stock_move_id and result.stock_move_id.with_user(SUPERUSER_ID) or self.env["stock.move"]
        line = {
            "id": result.id,
            "date": result.stock_move_date,
            "name": "[%s] %s" % (result.product_id.default_code, result.product_id.name),
            "product_id": result.product_id.id,
            "product_code": result.product_id.default_code,
            "product_name": result.product_id.name,
            "quantity": result.quantity,
            "unit_cost": result.unit_cost,
            "value": result.value,
            "uom": result.uom_id.name,
            "remark": result.description if not stock_move else "",
            "reference": result.stock_move_id and result.stock_move_id.reference or "Valuation",
            "stock_move": stock_move,
            "picking": stock_move.picking_id.with_user(SUPERUSER_ID) if stock_move.picking_id else self.env["stock.picking"],
            "sale_line": stock_move.sale_line_id.with_user(SUPERUSER_ID) if stock_move.sale_line_id else self.env["sale.order.line"],
            "purchase_line": stock_move.purchase_line_id.with_user(SUPERUSER_ID) if stock_move.purchase_line_id else self.env["purchase.order.line"]
        }

        # Options caret for picking, production and unbuild
        if hasattr(stock_move, "picking_id") \
            or hasattr(stock_move, "production_id") \
            or hasattr(stock_move, "raw_material_production_id") \
            or hasattr(stock_move, "unbuild_id"):
            line.update({"transfer": line["picking"] or stock_move.production_id or stock_move.raw_material_production_id or stock_move.unbuild_id})
   
        # Options caret for order line
        if line["sale_line"] or line["purchase_line"]:
            line.update({"order_line": line["sale_line"] if line["sale_line"] else line["purchase_line"]})

        # Options Show Analytic Account
        if options.get("show_analytic_account"):
            analytic_account = ""
            if hasattr(stock_move, "analytic_account_id") and stock_move.analytic_account_id:
                if options['export_mode'] != 'print':
                    analytic_account = stock_move.analytic_account_id.code if stock_move.analytic_account_id.code else stock_move.analytic_account_id.display_name
                else:
                    analytic_account = stock_move.analytic_account_id and stock_move.analytic_account_id.display_name or ""
            line.update({"analytic_account": analytic_account})

        # Options Show Stock Account
        if options.get("show_stock_account"):
            line.update({"stock_account": result.stock_account_id and result.stock_account_id.display_name or ""})
        return line

    def _build_line_groupby(self, group_lines, results, options, reverse=False) -> dict:
        """ This method is used to build the line dict for the group line based on the grouping field."""
        if options["stock_grouping_field"] == "account_id":
            accounts = results.mapped("stock_account_id")
            for account in accounts:
                res = results.filtered(lambda r: r.stock_account_id == account)
                group_lines.update(self._build_line_groupby_dic(account.id, account.code, account.name, res, reverse=reverse))
            group_lines.update(self._build_line_groupby_dic(0, "", "Undefined", results.filtered(lambda r: not r.stock_account_id), reverse=reverse))
        elif options["stock_grouping_field"] == "analytic": 
            analytics = results.mapped("stock_move_id.analytic_account_id")
            for analytic in analytics:
                res = results.filtered(lambda r: r.stock_move_id.analytic_account_id == analytic)
                group_lines.update(self._build_line_groupby_dic(analytic.id, analytic.code, analytic.name, res, reverse=reverse))
            group_lines.update(self._build_line_groupby_dic(0, "", "Undefined", results.filtered(lambda r: not r.stock_move_id.analytic_account_id), reverse=reverse))
        else:
            group_lines.update(self._build_line_groupby_dic(0, "no_group", "No Grouping", results, reverse=reverse))
        return group_lines

    def _build_line_groupby_dic(self, group_id, group_code, group_name, results, reverse=False) -> dict:
        """ This method is used to build the line dict for the group line.
        :param group_id: The id of the group
        :param group_code: The code of the group
        :param group_name: The name of the group
        :param results: The recordset of stock.valuation.report that contains the values to display in the lines of the group
        :return: A dict containing the values of the line to display.
        """
        value = sum(results.mapped("value"))
        quantity = sum(results.mapped("quantity"))
        if reverse:
            value *= -1
            quantity *= -1
        return {
            group_id: {
                "group_code": group_code,
                "group_name": group_name, 
                "quantity": quantity,
                "value": value,
            }
        }

    def _build_line_markup(self, line_dic) -> tuple:
        """ This method is used to build the markup of the line.
        :param line_dic: The dict containing the values of the line
        :return: The markup to app  ly on the line (can be 'danger', 'warning', 'success' or 'info')
        """
        caret_options = "stock.reports"
        markup = {
            "stock.valuation.report": line_dic["id"]
        }
        for key in ["transfer", "stock_move", "order_line"]:
            if key in line_dic and line_dic[key]:
                if key == "transfer":   
                    caret_options = "stock.move.report"
                elif key == "order_line":
                    caret_options = "stock.move.report.order.line"
                markup.update({line_dic[key]._name: line_dic[key].id})
        return markup, caret_options

    def _get_domain_filter(self, report, options, expanded_line_ids=[]) -> list:
        """ This method is used to get the domain to apply on the stock.valuation.report model based on the options of the report.
        :param report: The report object -> account.reports
        :param options: The options of the report
        :return: A domain to apply on the stock.valuation.report model.
        """
        domain = []
        date_to = options['date']['date_to'] + ' ' + str(time.max.strftime("%H:%M:%S.%f"))
        date_from = options['date']['date_from'] + ' ' + str(time.min.strftime("%H:%M:%S.%f"))
        company_ids = report.get_report_company_ids(options)
        domain += [
            ("stock_move_date", ">=", date_from), 
            ("stock_move_date", "<=", date_to), 
            ("company_id", "in", company_ids),
        ]

        # Options Filter by tracked inventory 
        if options.get("track_inventory") and not options.get("none_track_inventory"):
            domain.append(("product_id.is_storable", "!=", False))
        elif not options.get("track_inventory") and options.get("none_track_inventory"):
            domain.append(("product_id.is_storable", "=", False))
    
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

        # Options Filter by analytic account
        if options.get("analytic_accounts"):
            domain.append(("stock_move_id.analytic_account_id", "in", options["analytic_accounts"]))

        # Options Filter by Analytic and line expanded by analytic
        if getattr(self.env["stock.move"], "analytic_account_id", False):
            if options["stock_grouping_field"] == "analytic" and expanded_line_ids:
                domain.append(("stock_move_id.analytic_account_id", "in", expanded_line_ids))
            elif options["analytic_accounts"]:
                 domain.append(("stock_move_id.analytic_account_id", "in", options["analytic_accounts"]))

        # Options Filter by line expanded by account_id
        if options["stock_grouping_field"] == "account_id" and expanded_line_ids:
            domain.append(("stock_account_id", "in", expanded_line_ids))

        return domain

    def _get_domain_filter_location(self, usage, usage_dest, domain=[]):
        """ This method is used to get the domain to apply on the stock.valuation.report model based on the location usage.
        :param domain: The initial domain to apply on the stock.valuation.report model.
        :param usage: The usage of the location (supplier, customer, internal, transit, production, equipment or inventory)
        :param usage_dest: The usage of the destination location (supplier, customer, internal, transit, production, equipment or inventory)
        :return: A domain to apply on the stock.valuation.report model based on the location usage.
        """
        return domain + [
            ("stock_move_id.move_line_ids.location_id.usage", "in", usage),
            ("stock_move_id.move_line_ids.location_dest_id.usage", "in", usage_dest)
        ]

    def _get_column_max_characters(self) -> dict:
        """ This method is used to get the max characters to display for each column.
        :return: A dict {expr_label: max_characters, ...}
        """
        return {}
    
    def _report_expand_unfoldable_line_stock_move_report(self, line_dict_id, groupby, options, progress, offset, unfold_all_batch_data=None):
        """ This method is used to expand the unfoldable lines of the stock move report.
        It is called when the user clicks on the unfoldable line and is responsible for returning the lines to display.
        :param line_dict_id: The dictionary ID of the line to expand (can be used to find the group_id and groupby field value)
        :param groupby: The field used to group the report
        :param options: The options of the report
        :param progress: The progress of the unfolding (from 0 to 1)
        :param offset: The offset of the lines to return (used for pagination)
        :param unfold_all_batch_data: If the unfolding is triggered by an "unfold all" action, this parameter contains a list of dicts with the data of all the lines to unfold. Otherwise, it is None.
        :return: {
            'lines': lines,
            'offset_increment': report.load_more_limit,
            'has_more': has_more,
            'progress': next_progress,
        }.
        """
        report = self.env['account.report'].browse(options['report_id'])
        model, line_id = report._get_model_info_from_id(line_dict_id)
        
        if model != 'account.account':
            raise UserError(_("Wrong ID for report line to expand: %s", line_dict_id))

        lines = []
        line_counter = 0
        has_more = False
        expanded_line_ids = []
        company_currency = self.env.company.currency_id
        quantity_digits = self.env['decimal.precision'].precision_get('Product Unit of Measure')

        if options.get("stock_grouping_field") != "none":
            expanded_line_ids = [False if line_id == 0 else line_id]
        
        limit_to_load = report.load_more_limit + 1 if report.load_more_limit and options['export_mode'] != 'print' else None
        group_lines, results = self._get_values(report, options, expanded_line_ids, offset=offset, limit=limit_to_load)
        for result in results:
            line_counter += 1
            if line_counter == limit_to_load:
                has_more = True
                break
            lines.append(self._build_line(report, options, line_dict_id, result, company_currency, quantity_digits))

        return {
            'lines': lines,
            'offset_increment': report.load_more_limit,
            'has_more': has_more,
        }
    
class StockIncomingReportCustomHandle(models.AbstractModel):
    _name = "stock.incoming.report.handler"
    _inherit = "stock.move.report.handler"
    _description = "Stock Incoming Report Custom Handler"

    ####################################################
    # INHERIT METHOD
    ####################################################
    def _custom_options_initializer(self, report, options, previous_options):
        """ To be overridden to add report-specific _init_options... code to the report. """
        super()._custom_options_initializer(report, options, previous_options=previous_options)
        # column_group_options_map = report._split_options_per_column_group(options)

        # Custom filters for incoming report    
        options["stock_grouping"] = "incoming"
        options["mrp_operation"] = previous_options.get("mrp_operation", True)
        options["purchase_to_stock"] = previous_options.get("purchase_to_stock", True)
        
        # custom options show columns for incoming
        options["show_inv_note"] = previous_options.get("show_inv_note", False)

    ####################################################
    # BUSSINESS METHOD
    ####################################################
    def _get_values(self, report, options, expanded_line_ids=[], offset=0, limit=None) -> tuple:
        # " Get the data from the database "
        model = self.env['stock.valuation.report']
        model.check_access('read')

        # parameters 
        group_lines = {}
        domain = self._get_domain_filter(report, options, expanded_line_ids)

        # Options Filter for incoming
        if not options.get("purchase_to_stock") \
            and not options.get("customer_return") \
            and not options.get("mrp_operation") \
            and not options.get("mrp_operation_return") \
            and not options.get("inventory_loss") \
            and not options.get("vendor_return"):
            domain.append(("stock_move_type", "=", "Incoming"))
        else:
            usage = []
            moves = model
            # Options Filter Purchase to stock (supplier -> internal or transit)
            if options.get("purchase_to_stock"):
                usage += ["supplier"]
                # include transit location to avoid missing move in case of intercompany purchase where the move from supplier is transit move
                moves |= model.search(self._get_domain_filter_location(["transit"], ["internal"], domain + [("stock_move_id.picking_code", "=", "incoming")]))

            # Options Filter Return from customer
            if options.get("customer_return"):
                usage += ["customer"]

            # Options Filter Inventory loss / adjusment
            if options.get("inventory_loss"):
                usage += ["inventory"]

            moves |= model.search(self._get_domain_filter_location(usage, ["internal", "transit"], domain))

            # Opttion Filter Make to stock (all move from production)
            if options.get("mrp_operation") and getattr(self.env["stock.move"], "production_id", False):
                moves |= model.search(self._get_domain_filter_location(["production"], ["internal", "transit"], domain + [("stock_move_id.production_id", "!=", False)]))

            # Options Filter Return from internal (production -> equipment or component)
            if options.get("mrp_operation_return"): 
                custom_domain = [("stock_move_id.origin_returned_move_id", "!=", False)]
                if getattr(self.env["stock.move"], "unbuild_id", False):
                    moves |= model.search(self._get_domain_filter_location(["internal", "transit"], ["production"], domain + [("stock_move_id.unbuild_id", "!=", False)]))
                moves |= model.search(self._get_domain_filter_location(["production", "equi"], ["internal", "transit"], custom_domain))
                moves |= model.search(self._get_domain_filter_location(["transit"], ["internal"], domain + custom_domain + [("stock_move_id.picking_code", "=", "internal")]))

            # Options Filter Return to vendor
            if options.get("vendor_return"):
                moves |= model.search(self._get_domain_filter_location(["internal", "transit"], ["supplier"], domain))
                moves |= model.search(self._get_domain_filter_location(["internal"], ["transit"], domain + [("stock_move_id.picking_code", "=", "outgoing")]))
            
            domain = [("id", "in", list(set(moves.ids)))]
           
        # search
        results = model.search(domain, offset=offset, limit=limit, order="create_date asc")
        
        # For first load group line
        if not expanded_line_ids: 
            group_lines = self._build_line_groupby(group_lines, results, options, reverse=False)
        return group_lines, results

    ####################################################
    # COLUMN/LINE HELPERS
    ####################################################
    def _get_column_max_characters(self) -> dict:
        return {
            "origin": 25,
            "partner": 30,
            "analytic_account": 20,
            "stock_account": 20,
            "counterpart_account": 20,
            "remark": 30,
        }
    
    def _build_line_dic(self, options, result) -> dict:
        line = dict(super(StockIncomingReportCustomHandle, self)._build_line_dic(options, result))

        # remark for incoming report with purchase line, 
        # we will display the department of purchase request line if exist, 
        # if not we will display the origin of purchase request if exist
        if line["purchase_line"] and hasattr(line["purchase_line"], "purchase_request_lines"):
            pr_lines = line["purchase_line"].purchase_request_lines.with_user(SUPERUSER_ID) or self.env[line["purchase_line"].purchase_request_lines._name]
            
            # check if field department_id exist in purchase.request.line
            if pr_lines and hasattr(pr_lines, "department_id"):
                line["remark"] = ", ".join(pr_lines.mapped("department_id.name")) 

            # check field orderpoint_id exist in purchase.request.line
            orderpoints = line["purchase_line"].filtered(lambda p: p.orderpoint_id).mapped("orderpoint_id.name")
            if pr_lines and hasattr(pr_lines, "orderpoint_id"):
                orderpoints += pr_lines.mapped("orderpoint_id.name")
            
            if orderpoints:
                orderpoints = list(set(orderpoints))
                line["remark"] += ", %s" % ", ".join(orderpoints) if line["remark"] else ", ".join(orderpoints)
            elif pr_lines.filtered(lambda p: p.request_id and p.request_id.origin):
                origins = pr_lines.mapped("request_id.origin")
                line["remark"] += ", %s" % ", ".join(origins) if line["remark"] else ", ".join(origins)
        else:
            line["remark"] = ""
            
        # Options Show Source 
        if options.get("show_origin"):
            origin = line["stock_move"] and line["stock_move"].origin or ""
            if result.quantity < 0 and line["stock_move"].origin_returned_move_id:
                origin = "Return of %s" % line["stock_move"].origin_returned_move_id.reference
                if line["stock_move"].origin_returned_move_id.purchase_line_id:
                    origin = "%s / %s" % (origin, line["stock_move"].origin_returned_move_id.purchase_line_id.order_id.name) 
            elif line["purchase_line"] and hasattr(line["purchase_line"], "purchase_request_lines") and line["purchase_line"].purchase_request_lines:
                pr_lines = line["purchase_line"].purchase_request_lines.with_user(SUPERUSER_ID) or self.env[line["purchase_line"].purchase_request_lines._name]
                origin += ' / %s' % (', '.join(pr_lines.mapped('request_id.name')),)
            elif hasattr(line["stock_move"], "unbuild_id") and line["stock_move"].unbuild_id:
                origin += "Unbuild of %s" % line["stock_move"].unbuild_id.mo_id.name
            line.update({"origin": origin})

        # Options Show Partner
        if options.get("show_partner"):
            partner = line["picking"] and line["picking"].partner_id
            if not partner and line["sale_line"]:
                partner = line["sale_line"].order_partner_id
            if not partner and line["purchase_line"]:
                partner = line["purchase_line"].partner_id
            line.update({"partner": partner and partner.name or ""})

        # Options Show Invoice/Delivery Note
        if options.get("show_inv_note"):
            line.update({"inv_note": line["picking"] and getattr(line["picking"], "bill_inv_no", "") or ""})
        
        # Options Show Counterpart Account
        if options.get("show_counterpart_account"):
            counterpart_account = ""
            if result.account_move_id:
                if result.quantity > 0:
                    move_line = result.account_move_id.line_ids.filtered(lambda l: l.product_id.id == result.product_id.id and l.debit > 0)[0]
                else:
                    move_line = result.account_move_id.line_ids.filtered(lambda l: l.product_id.id == result.product_id.id and l.credit > 0)[0]
                counterpart_account = move_line.account_id.display_name
            line.update({"counterpart_account": counterpart_account})
        return line
       
class StockOutgoingReportCustomHandle(models.AbstractModel):
    _name = "stock.outgoing.report.handler"
    _inherit = "stock.move.report.handler"
    _description = "Stock Outgoing Report Custom Handler"

    ####################################################
    # INHERIT METHOD
    ####################################################
    def _custom_options_initializer(self, report, options, previous_options):
        """ To be overridden to add report-specific _init_options... code to the report. """
        super()._custom_options_initializer(report, options, previous_options=previous_options)
        # column_group_options_map = report._split_options_per_column_group(options)

        # Custom filters for outgoing report
        options["stock_grouping"] = "outgoing"
        options["sale_to_customer"] = previous_options.get("sale_to_customer", True)
        options["mrp_operation"] = previous_options.get("mrp_operation", True)

        # custom options show columns for outgoing
        options["show_machine"] = previous_options.get("show_machine", True)

    ####################################################
    # BUSSINESS METHOD
    ####################################################
    def _get_values(self, report, options, expanded_line_ids=[], offset=0, limit=None) -> tuple:
        # " Get the data from the database "
        model = self.env['stock.valuation.report']
        model.check_access('read')

        # parameters 
        group_lines = {}
        domain = self._get_domain_filter(report, options, expanded_line_ids) 

        # Options Filter for Outgoing # and not options.get("delivery_internal") \
        if not options.get("sale_to_customer") \
            and not options.get("customer_return") \
            and not options.get("mrp_operation_return") \
            and not options.get("vendor_return") \
            and not options.get("inventory_loss"):
            domain.append(("stock_move_type", "=", "Outgoing"))
        else:
             # Options Filter Receipt to stock 
            usage_dest = []
            if options.get("sale_to_customer"):
                usage_dest += ["customer"]

            # Options Filter Delivery to internal
            if options.get("mrp_operation"):
                usage_dest += [ "production", "equi"]   

            # Options Filter Inventory loss / adjusment
            if options.get("inventory_loss"):
                usage_dest += ["inventory"]

            # Options Filter Return to vendor
            if options.get("vendor_return"):
                usage_dest += ["supplier"]

            outgoing_domain = self._get_domain_filter_location(["internal", "transit"], usage_dest)

            # Options Filter Return from production or Return from customer
            if options.get("mrp_operation_return") or options.get("customer_return"):
                moves = model.search(domain + outgoing_domain) if usage_dest else model

                # Return from production (production/equipment)
                if options.get("mrp_operation_return"):
                    moves |= model.search(self._get_domain_filter_location(["production", "equi"], ["internal", "transit"], domain + [("stock_move_id.origin_returned_move_id", "!=", False)]))

                # Return from customer
                if options.get("customer_return"):
                    moves |= model.search(self._get_domain_filter_location(["customer"], ["internal", "transit"], domain))

                domain = [("id", "in", list(set(moves.ids)))]
            else:
                domain += outgoing_domain
         
        # search 
        results = model.search(domain, offset=offset, limit=limit, order="create_date asc")
        
        # For first load group line
        if not expanded_line_ids: 
            group_lines = self._build_line_groupby(group_lines, results, options, reverse=True)
        return group_lines, results

    ####################################################
    # COLUMN/LINE HELPERS
    ####################################################
    def _get_column_max_characters(self) -> dict:
        return {
            "origin": 25,
            "partner": 30,
            "machine": 20, 
            "analytic_account": 20,
            "stock_account": 20,
            "counterpart_account": 20,
            "remark": 30,
        }
    
    def _build_line_dic(self, options, result) -> dict:
        line = dict(super(StockOutgoingReportCustomHandle, self)._build_line_dic(options, result))

        # reverse value and quantity for outgoing report to display positive values in the report
        line["value"] = line["value"] * -1
        line["quantity"] = line["quantity"] * -1

        # compute machine and remark for outgoing report:
        line.update({"machine": ""})
        if hasattr(line["stock_move"], "maintenance_id") and line["stock_move"].maintenance_id:
            machine = ""
            mro = line["stock_move"].maintenance_id.with_user(SUPERUSER_ID)
            if line["picking"] and hasattr(line["picking"], "requisition_picking_id") and line["picking"].requisition_picking_id:
                machine = (line["picking"].requisition_picking_id.reason_for_requisition or "").strip()
                if machine != "":
                    if hasattr(mro, "request_id") and mro.request_id:
                        machine = machine.replace("[%s (%s)] \nEquipment: " % (mro.request_id.name, mro.name), "").split("\n")[0]
                    else:
                        machine = machine.replace("[%s]  \nEquipment: " % mro.name, "").split("\n")[0]
            asset_name = (mro.equipment_id.asset_id or "").strip()
            line["machine"] = "%s: %s" % (asset_name, machine or mro.equipment_id.name)
            line["remark"] = mro.cause or machine
        # if not maintenance, check if requisition exist on picking and get machine from requisition
        elif line["stock_move"] and line["picking"] and (hasattr(line["picking"], "requisition_vehicle_id") or hasattr(line["picking"], "requisition_picking_id")):
            if line["picking"].requisition_picking_id:
                requisition = line["picking"].requisition_picking_id.with_user(SUPERUSER_ID)
                requisition_lines = requisition.requisition_line_ids.filtered(lambda r: r.product_id.id == line["stock_move"].product_id.id)
                if line["picking"].requisition_vehicle_id:
                    vehicle = line["picking"].requisition_vehicle_id.with_user(SUPERUSER_ID)
                    line["machine"] = "%s: %s" % (vehicle.asset_ref, vehicle.display_name) if hasattr(vehicle, "asset_ref") and vehicle.asset_ref else vehicle.display_name
                    requisition_lines = requisition_lines.filtered(lambda r: r.vehicle_id.id == vehicle.id)
                line_remark = ", ".join(requisition_lines.mapped(lambda r: r.remark or ''))
                line["remark"] = line_remark or requisition.reason_for_requisition or ""
        
        # Options Show Source 
        if options.get("show_origin"):
            origin = line["stock_move"] and line["stock_move"].origin or ""
            if result.quantity > 0 and line["stock_move"].origin_returned_move_id:
                origin = "Return of %s" % line["stock_move"].origin_returned_move_id.reference
                if line["stock_move"].origin_returned_move_id.sale_line_id:
                    origin = "%s / %s" % (origin, line["stock_move"].origin_returned_move_id.sale_line_id.order_id.name)
                elif line["stock_move"].origin_returned_move_id.purchase_line_id:
                     origin = "%s / %s" % (origin, line["stock_move"].origin_returned_move_id.purchase_line_id.order_id.name)
            elif line["purchase_line"] and hasattr(line["purchase_line"], "purchase_request_lines") and line["purchase_line"].purchase_request_lines:
                pr_lines = line["purchase_line"].purchase_request_lines.with_user(SUPERUSER_ID)
                origin += ' / %s' % (', '.join(pr_lines.mapped('request_id.name')),)
            elif hasattr(line["stock_move"], "unbuild_id") and line["stock_move"].unbuild_id:
                origin += "Unbuild of %s" % line["stock_move"].unbuild_id.mo_id.name
            line.update({"origin": origin})
        
        # Options Show Partner
        if options.get("show_partner"):
            partner = line["picking"] and line["picking"].partner_id.name or ""
            if not partner and line["picking"] and hasattr(line["picking"], "requisition_picking_id") and line["picking"].requisition_picking_id:
                if hasattr(line["picking"].requisition_picking_id, "employee_id") and line["picking"].requisition_picking_id.employee_id:
                    partner = line["picking"].requisition_picking_id.employee_id.display_name
                elif hasattr(line["picking"], "user_id") and line["picking"].user_id:
                    partner = line["picking"].user_id.display_name
            if not partner and line["sale_line"]:
                partner = line["sale_line"].order_partner_id.name or ""
            if not partner and line["purchase_line"]:
                partner = line["purchase_line"].partner_id.name or ""
            line.update({"partner": partner})

        # Options Show Counterpart Account
        if options.get("show_counterpart_account"):
            counterpart_account = ""
            if result.account_move_id:
                if result.quantity > 0:
                    move_line = result.account_move_id.line_ids.filtered(lambda l: l.product_id.id == result.product_id.id and l.debit > 0)[0]
                else:
                    move_line = result.account_move_id.line_ids.filtered(lambda l: l.product_id.id == result.product_id.id and l.credit > 0)[0]
                counterpart_account = move_line.account_id.display_name
            line.update({"counterpart_account": counterpart_account})

        return line