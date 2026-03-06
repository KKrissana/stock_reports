# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

import datetime

from odoo import fields, models, tools, _
from odoo.tools import format_date, SQL, Query
from odoo.tools import DEFAULT_SERVER_DATE_FORMAT as DATE_FORMAT
from odoo.exceptions import UserError

from collections import defaultdict
from datetime import datetime, time, timedelta

MAX_NAME_LENGTH = 50

class AgedStockCustomHandle(models.AbstractModel):
    _name = "stock.aged.report.handler"
    _inherit = "account.report.custom.handler"
    _description = "Aged Stock Custom Handler"

    ####################################################
    # INHERIT METHOD
    ####################################################
    def _get_custom_display_config(self):
        return {
            "components": {
                "AccountReportFilters": "stock_reports.AgedStockFilters",
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

        as_of_datetime = datetime.combine(datetime.strptime(options['date']['date_to'], DATE_FORMAT).date(), time.max)
        interval = options['aged_stock_interval']
        company_currency = self.env.company.currency_id
        quantity_digits = self.env['decimal.precision'].precision_get('Product Unit of Measure')
        column_expression = self.env['account.report.expression']

        # get value 
        products, product_moves = self._get_values(report, options)
        totals = self._get_calc_columns(options)
        last_period_column = len(report.column_ids.filtered(lambda c: "value_period" in c.expression_label))

        # calculate for total line and group line
        for move in product_moves:
            product = products.filtered(lambda p: p.id == move["product_id"][0])
            last_move_date = move["date"]
            days = (as_of_datetime - last_move_date).days
            days = days if days else 1
            for column in options["columns"]:
                expr_label = column['expression_label']

                # Summary for total line 
                if expr_label in totals.keys() :
                    column_value = 0
                    if "qty_period" in expr_label:
                        column_value = self._get_period_column_value(product, "qty_period", expr_label, interval, days, last_period_column)
                    elif "value_period" in expr_label:
                        column_value = self._get_period_column_value(product, "value_period", expr_label, interval, days, last_period_column)
                    elif "total" in expr_label:
                        column_value = product["qty_available"] if "qty" in expr_label else product["value_svl"]

                    totals[expr_label] += column_value 

        # Report group and total line
        columns = []
        for column in options["columns"]:
            expr_label = column['expression_label']
            column_value = totals[expr_label] if expr_label in totals.keys() else None
            columns.append(report._build_column_dict(column_value, column, options=options, currency=company_currency, digits=quantity_digits, column_expression=column_expression))

        
        lines.append({
            'id': report._get_generic_line_id("product.product", 0),
            'name': "No Grouping",
            'columns': columns,
            'level': 1,
            'unfoldable': True,
            'unfolded': True,
            'class': 'd-none',
            'expand_function': '_report_expand_unfoldable_line_aged_stock', 
        })
        lines.append({
            'id': report._get_generic_line_id(None, None, markup='total'),
            'name': _('Total'),
            'level': 0,
            'columns': columns,
        })

        return [(0, line) for line in lines]

    def _caret_options_initializer(self):
        return {
            "stock.reports": [
                {"name": _("Product"), "action": "open_product"}
            ]
        }
    
    def _custom_options_initializer(self, report, options, previous_options):
        """ To be overridden to add report-specific _init_options... code to the report. """
        super()._custom_options_initializer(report, options, previous_options=previous_options)
        column_group_options_map = report._split_options_per_column_group(options)

        # Options standard account_reports
        options["all_entries"] = None
        options["ignore_totals_below_sections"] = True

        # Custom Handler Options,
        options["aged_stock_base_on"] = previous_options.get("aged_stock_base_on") or "movement"
        options["aged_stock_product_control"] = previous_options.get("aged_stock_product_control") or "all"
        options["aged_stock_interval"] = previous_options.get("aged_stock_interval") or 30

        # Set aging column names
        interval = options['aged_stock_interval']
        period_number = 0
        options['custom_columns_subheaders'] = [
            {"name": _("Characteristics"), "colspan": 3},
            {"name": _("On Hand as of %s" % format_date(self.env, options['date']['date_to'])), "colspan": 2},
            {"name": _(f"Less than {interval + 1} days"), "colspan": 2}
        ]
        for column in options["columns"]:
            # Dynamic naming of columns containing period 
            # column_group_options =  column_group_options_map[column['column_group_key']]
            
            if "qty_period" in column["expression_label"]:
                period_number = int(column['expression_label'].replace('qty_period', '')) - 1
                if 0 < period_number < 6:
                    options['custom_columns_subheaders'] += [{"name": _(f"{interval * period_number + 1} - {interval * (period_number + 1)}"), "colspan": 2}]
        
        options['custom_columns_subheaders'] += [
            {"name": _(f"Older than {interval * period_number} days"), "colspan": 2},
            
        ]

    ####################################################
    # BUSSINESS METHOD
    ####################################################
    def _get_values(self, report, options, expanded_line_ids=[], offset=0, limit=None):
        # " Get the data from the database "
        self.env['product.product'].check_access('read')
        self.env['stock.move.line'].check_access('read')

        # Parmeters
        as_of_datetime = options['date']['date_to'] + ' ' + str(time.max.strftime("%H:%M:%S"))
        product_context = {
            "to_date": as_of_datetime,
            "allowed_company_ids": [self.env.company.id],
            "prefetch_fields": False
        }

        # Domain Filters for product.product 
        domain = [("is_storable", "=", True)]

        # Options Filter by product and Categories
        selected_products, selected_product_categories = report._get_options_product(options)
        if selected_products:
            domain.append(("id", "in", selected_products))
        else: # Case not filter product, get all product available qty
            domain += self.env["product.product"].with_context(product_context)._search_qty_available("!=", 0)
        
        if selected_product_categories:
            domain.append(("categ_id", "in", selected_product_categories))

        # Options Filters by Stock rules 
        if options["aged_stock_product_control"] == "none":
            domain.append(("orderpoint_ids", "=", False))
        elif options["aged_stock_product_control"] == "orderpoint":
            domain.append(("orderpoint_ids.company_id", "=", self.env.company.id))
        
        # TODO: Still optimization possible when searching virtual quantities
        # Order the search on `id` to prevent the default order on the product name which slows
        # down the search because of the join on the translation table to get the translated names.
        products = self.env["product.product"].with_context(product_context).search(domain, offset=offset, limit=limit, order='id')

        # Filters for stock.move.line
        domain = [("date", "<=", as_of_datetime), ("product_id", "in", products.ids)]
        
        # Options Filters by Based move
        if options.get("aged_stock_base_on") == "incoming":
            domain.append(("location_id.usage", "not in", ["internal"]))
            domain.append(("location_dest_id.usage", "in", ["internal"]))
        elif options.get("aged_stock_base_on") == "outgoing":
            domain.append(("location_id.usage", "in", ["internal"]))
            domain.append(("location_dest_id.usage", "not in", ["internal"]))

        product_moves = self.env["stock.move.line"].read_group(
            domain=domain, 
            fields=["product_id", "date:max"], 
            groupby=["product_id"], 
            orderby="product_id", 
            lazy=False
        )
        return products, product_moves
    
    def _get_period_column_value(self, product, key, expr_label, interval, days, last_period_column):
        column_value, period_number = 0.0, 0
        if key in expr_label:
            period_number = int(expr_label.replace(key, ""))
            max_day_period = period_number * interval
            if (max_day_period - interval) < days <= max_day_period \
                or (period_number == last_period_column and days > max_day_period):
                column_value = product["qty_available"] if "qty" in key else product["value_svl"]
        return column_value

    ####################################################
    # COLUMN/LINE HELPERS
    ####################################################
    def _get_calc_columns(self, options) -> dict[str, float]:
        total_columns = {}
        for column in options["columns"]:
            expr_label = column['expression_label']
            if column["figure_type"] in ["integer", "float", "monetary"]:
                total_columns[expr_label] = 0.0
            elif column["figure_type"] == "integer":
                total_columns[expr_label] = 0
        return total_columns

    def _get_report_line (self, report, options, parent_line_id, line_dic, move_dic, as_of_datetime, last_period_column):
        company_currency = self.env.company.currency_id
        quantity_digits = self.env['decimal.precision'].precision_get('Product Unit of Measure')
        interval = options['aged_stock_interval']
        last_move_date = move_dic["date"]
        days = (as_of_datetime - last_move_date).days

        columns = []
        name = line_dic["display_name"]

        for column in options["columns"]:
            column_group_key = column['column_group_key']
            expr_label = column['expression_label']
            column_value = 0 if column["figure_type"] in ["integer", "float", "monetary"] else None
            if "days" == expr_label:
                column_value = days
                days = days if days else 1 
            elif "qty_period" in expr_label:
                column_value = self._get_period_column_value(line_dic, "qty_period", expr_label, interval, days, last_period_column)
            elif "value_period" in expr_label:
                column_value = self._get_period_column_value(line_dic, "value_period", expr_label, interval, days, last_period_column)
            elif "total" in expr_label:
                column_value = line_dic["qty_available"] if "qty" in expr_label else line_dic["value_svl"]
            else:
                column_value = line_dic[expr_label] or None
            columns.append(report._build_column_dict(column_value, column, options=options, currency=company_currency, digits=quantity_digits))

        # Report detail line 
        line = {
            'id': report._get_generic_line_id('product.product', line_dic["id"], parent_line_id=parent_line_id),
            'level': 2,
            'name': name,
            'columns': columns,
            'parent_id': parent_line_id,
            'unfoldable': False,
            'unfolded': False,
            'caret_options': 'stock.reports',
            'maxCharacters': MAX_NAME_LENGTH,
        }
        # set title
        if len(name) > MAX_NAME_LENGTH:
            line.update({'title_hover': name})
        return line

    # METHOD: For calling back when expanding a line or expanding all - depends on the line function 'expand_function'.
    def _report_expand_unfoldable_line_aged_stock(self, line_dict_id, groupby, options, progress, offset, unfold_all_batch_data=None):
        lines = []
        report = self.env['account.report'].browse(options['report_id'])
        as_of_datetime = datetime.combine(datetime.strptime(options['date']['date_to'], DATE_FORMAT).date(), time.max)
        
        # get line values
        last_period_column = len(report.column_ids.filtered(lambda c: "value_period" in c.expression_label))
        has_more = False
        line_counter = 0
        limit_to_load = report.load_more_limit + 1 if report.load_more_limit and options['export_mode'] != 'print' else None
        products, product_moves = self._get_values(report, options, [], offset=offset, limit=limit_to_load)
        for move in product_moves:
            line_counter += 1
            if line_counter == limit_to_load:
                has_more = True
                break
            product = products.filtered(lambda p: p.id == move["product_id"][0])
            lines.append(self._get_report_line(report, options, line_dict_id, product, move, as_of_datetime, last_period_column))

        return {
            'lines': lines,
            'offset_increment': report.load_more_limit,
            'has_more': has_more,
            #'progress': next_progress,
        }
