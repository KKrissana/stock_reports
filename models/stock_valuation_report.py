# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import fields, models, tools, _
from odoo.tools import format_date, SQL, Query
from odoo.exceptions import UserError

from collections import defaultdict
from datetime import datetime, time, timedelta

MAX_NAME_LENGTH = 50

class StockValuationReport(models.Model):
    _name = "stock.valuation.report"
    _inherit = ["stock.valuation.layer"]
    _description = "Stock Valuation"
    _order = "create_date, id"
    _auto = False
    _log_access = True # Include magic fields
    _rec_name = "product_id"

    stock_move_date = fields.Datetime('Move Date', readonly=True)
    stock_account_id = fields.Many2one('account.account', 'Valuation Account', readonly=True)
    stock_move_type = fields.Char('Move Type', readonly=True)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(self._sql_function())
        self.env.cr.execute("""CREATE or REPLACE VIEW %s AS (
            SELECT svl.*,
                COALESCE(sm.date, svl.create_date)          AS stock_move_date,
				get_account_aml(svl.account_move_id, svl.product_id, svl.quantity, svl.value) AS stock_account_id,
                CASE 
                    WHEN svl.quantity > 0 OR svl.value > 0 THEN 'Incoming'
                    WHEN svl.quantity < 0 OR svl.value < 0 THEN 'Outgoing' 
                    ELSE null
                END stock_move_type
            FROM stock_valuation_layer svl
                LEFT JOIN stock_move sm ON sm.id = svl.stock_move_id 
        )""" % self._table)

    def _sql_function(self):
        return """
            DROP FUNCTION IF EXISTS get_account_aml(integer, integer, double precision, double precision);
            CREATE OR REPLACE FUNCTION public.get_account_aml(
                in_move_id integer,
                in_product_id integer,
                in_quantity double precision,
                in_value double precision
            )
                RETURNS integer
                LANGUAGE 'plpgsql'
                COST 100
                VOLATILE PARALLEL UNSAFE
            AS $BODY$
            DECLARE
                out_account_id integer;
            BEGIN

                IF in_move_id is not null THEN 
                    SELECT aml.account_id 
                    INTO out_account_id
                    FROM account_move_line aml 
                    WHERE aml.move_id = in_move_id 
                    AND aml.product_id = in_product_id 
                    AND CASE 
                                WHEN in_quantity > 0 OR in_value > 0 THEN aml.debit
                                WHEN in_quantity < 0 OR in_value < 0 THEN aml.credit
                                ELSE 0
                            END > 0
                    ORDER BY aml.create_date
                    LIMIT 1;

                    IF out_account_id is null AND (in_value != 0 OR in_quantity != 0) THEN 
                        SELECT aml.account_id 
                        INTO out_account_id
                        FROM account_move_line aml 
                        WHERE aml.move_id = in_move_id 
                        AND aml.product_id IS NOT null 
                        AND CASE 
                                    WHEN in_quantity > 0 OR in_value > 0 THEN aml.debit
                                    WHEN in_quantity < 0 OR in_value < 0 THEN aml.credit
                                    ELSE 0
                                END > 0
                        ORDER BY aml.create_date
                        LIMIT 1;
                    END IF;

                    IF out_account_id is null AND in_value = 0 THEN
                        SELECT aml.account_id 
                        INTO out_account_id
                        FROM account_move_line aml 
                            LEFT JOIN account_account account ON account.id = aml.account_id
                        WHERE aml.move_id = in_move_id 
                        AND aml.product_id = in_product_id
                        AND account.account_type = 'asset_current'
                        ORDER BY aml.create_date
                        LIMIT 1;
                    END IF;
                END IF;
                RETURN out_account_id;
            END;
            $BODY$;
        """

    def action_view_stock_valuation_layers(self):
        view_id = self.env.ref('stock_reports.stock_valuation_layer_form_modify').id
        return {
            'name': 'Valuation',
            'type': 'ir.actions.act_window',
            'res_model': 'stock.valuation.layer',
            'view_mode': 'form',
            'view_id': view_id,
            'views': [(view_id, 'form')],
            'res_id': self.id,
        }


class StockValuationReportCustomHandle(models.AbstractModel):
    _name = "stock.valuation.report.handler"
    _inherit = "account.report.custom.handler"
    _description = "Stock Valuation Report Custom Handler"

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
        totals = self._get_calc_columns().copy()

        # calculate for total line and group line
        for res in results: 
            group_id = 0 if res.get("group_id", 0) is None else res.get("group_id", 0) 
            for column in options["columns"]:
                expr_label = column['expression_label']
                column_value = res[expr_label]

                # Summary by group id
                group_line = group_lines[group_id]
                if group_line and expr_label in group_line.keys():
                    group_line[expr_label] += column_value if column_value else 0.0

                # Summary for total line
                if expr_label in totals.keys():
                    totals[expr_label] += column_value if column_value else 0.0

        # Report group line (group by account id)
        for group_id in group_lines.keys():
            group_line = group_lines[group_id]
            columns = []
            for column in options["columns"]:
                expr_label = column['expression_label']
                column_value = group_line.get(expr_label, None)
                columns.append(report._build_column_dict(column_value, column, options=options, column_expression=column_expression, currency=company_currency))
            
            line_id = report._get_generic_line_id('account.account', group_id)
            is_in_unfolded_lines = True if not is_groupby else any(report._get_res_id_from_line_id(line_id, 'account.account') == group_id for line_id in options.get('unfolded_lines'))

            lines.append({
                'id': line_id,
                'name': "%s %s" % (group_line.get("group_code") if group_id != 0 else "", group_line.get("group_name")),
                'columns': columns,
                'level': 1,
                'unfoldable': True,
                'unfolded': is_in_unfolded_lines or options.get('unfold_all'),
                'class': 'd-none' if not is_groupby else '',
                'expand_function': '_report_expand_unfoldable_line_stock_valuation',
            })

        # Report total line
        columns = []
        for column in options["columns"]:
            expr_label = column['expression_label']
            column_value = totals[expr_label] if expr_label in totals.keys() else None
            columns.append(report._build_column_dict(column_value, column, options=options, currency=company_currency, digits=quantity_digits, column_expression=column_expression))

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
                {"name": _("Product"), "action": "open_product"},
                {"name": _("Valuation"), "action": "open_valuation"}
            ]
        }

    def _custom_options_initializer(self, report, options, previous_options):
        """ To be overridden to add report-specific _init_options... code to the report. """
        super()._custom_options_initializer(report, options, previous_options=previous_options)
        column_group_options_map = report._split_options_per_column_group(options)

        for col in options['columns']:
            # Dynamic naming of columns containing dates
            column_group_options = column_group_options_map[col['column_group_key']]
            if col['expression_label'] in ['qty_init', 'value_init']:
                col['name'] = format_date(self.env, column_group_options['date']['date_from'])
            elif col['expression_label'] in ['qty_balance', 'value_balance']:
                col['name'] = format_date(self.env, column_group_options['date']['date_to'])

        options['custom_columns_subheaders'] = [
            {"name": _("Characteristics"), "colspan": 2},
            {"name": _("Quantity"), "colspan": 4},
            {"name": _("Valuation"), "colspan": 4},
        ]

        # Options standard account_reports
        options["all_entries"] = None
        options["ignore_totals_below_sections"] = True
        

        # Custom Handler Options,  Group by account by default
        options["stock_valuation_type"] = previous_options.get('stock_valuation_type') or "all"
        options["stock_grouping"] = "valuation"
        options['stock_grouping_field'] = previous_options.get('stock_grouping_field') or "account_id"
        options["track_inventory"] = previous_options.get("track_inventory", True)
        options["none_track_inventory"] = previous_options.get("none_track_inventory", False)

    ####################################################
    # BUSSINESS METHOD
    ####################################################
    def _get_values(self, report, options, expanded_line_ids=[], offset=0, limit=None):
        # " Get the data from the database "
        self.env['stock.valuation.report'].check_access('read')

        # prepare parameter 
        query_cluase = {
            "select_cluase": "",
            "where_cluase": "valuation.company_id in %(company_ids)s AND valuation.stock_move_date <= %(date_to)s ",
            "groupby_cluase": "",
            "orderby_cluase": "",
        }
        
        company_ids = report.get_report_company_ids(options) # tuple(company for company in report.get_report_company_ids(options))
        date_to = options['date']['date_to'] + ' ' + str(time.max.strftime("%H:%M:%S.%f"))
        date_from = options['date']['date_from'] + ' ' + str(time.min.strftime("%H:%M:%S.%f"))

        # Options Filter by tracked inventory 
        if options.get("track_inventory") and not options.get("none_track_inventory"):
            query_cluase["where_cluase"] += " AND template.is_storable = 't' "
        elif not options.get("track_inventory") and options.get("none_track_inventory"):
            query_cluase["where_cluase"] += " AND (template.is_storable = 'f' OR template.is_storable IS null) "

        # Options Filter by product and Categories
        selected_products, selected_product_categories = report._get_options_product(options)
        if selected_products:
            query_cluase["where_cluase"] += " AND valuation.product_id in %(selected_products)s "
        
        if selected_product_categories:
            query_cluase["where_cluase"] += " AND template.categ_id in %(selected_product_categories)s "

        # Options Filter by line expanded (account_id)
        if expanded_line_ids:
            query_cluase["where_cluase"] += " AND COALESCE(valuation.stock_account_id, 0) in %(expanded_line_ids)s "

        # Options Filter by valuation type 
        if options["stock_valuation_type"] == "manual_periodic":
            query_cluase["where_cluase"] += " AND valuation.stock_account_id is null "
        elif options["stock_valuation_type"] == "real_time":
            query_cluase["where_cluase"] += " AND valuation.stock_account_id is not null "

        # Options Group by account_id
        group_lines = {}
        group_id, group_code, group_name = 0, "no_group", "No Grouping" 
        group_defaul = self._get_calc_columns().copy()
        group_defaul.update({"group_code": group_id, "group_name": group_name})
        # group_lines = [{"group_id": group_id, "group_code": group_id, "group_name": group_name}]
        if options.get("stock_grouping_field") == "account_id":
            group_id = SQL("valuation.stock_account_id")
            group_code = self.env["account.account"]._field_to_sql("account", "code_store")
            group_name = self.env["account.account"]._field_to_sql("account", "name")
            self._flush()
            self.env.cr.execute(SQL(
                self._get_query_group(query_cluase),
                group_id=group_id,
                group_code=group_code,
                group_name=group_name,
                company_ids=tuple(company_ids),
                date_to=date_to,
                selected_products=tuple(selected_products),
                selected_product_categories=tuple(selected_product_categories),
                expanded_line_ids=tuple(expanded_line_ids)
            ))
            results = self.env.cr.dictfetchall()
            for res in results:
                res_id = res.get("group_id")
                group = group_defaul.copy()
                group.update({"group_code": res.get("group_code"), "group_name": res.get("group_name") })
                if res_id is None:
                    res_id = 0
                    group["group_name"] = "Manual"
                group_lines.update({res_id: group})
        else: 
            group_lines.update({group_id: group_defaul.copy()})
  
        # Get summary values
        product_code = self.env["product.template"]._field_to_sql("template", "default_code")
        product_name = self.env["product.template"]._field_to_sql("template", "name")
        uom_name = self.env["uom.uom"]._field_to_sql("uom", "name")
        self.env.cr.execute(SQL(
            self._get_query_sum(query_cluase, offset, limit),
            product_code=product_code,
            product_name=product_name,
            uom_name=uom_name,
            group_id=group_id,
            group_code=group_code,
            group_name=group_name,
            quantity_digits=self.env['decimal.precision'].precision_get('Product Unit of Measure'),
            value_digits=self.env.company.currency_id.decimal_places,
            company_ids=tuple(company_ids),
            date_to=date_to,
            date_from=date_from,
            selected_products=tuple(selected_products),
            selected_product_categories=tuple(selected_product_categories),
            expanded_line_ids=tuple(expanded_line_ids),
            offset=offset,
            limit=limit
        ))
        results = self.env.cr.dictfetchall()
        return group_lines, results

    def _get_query_group(self, query_cluase) -> str:
        query_cluase["select_cluase"] = """, 
            %(group_id)s     AS group_id,
            %(group_code)s   AS group_code,
            %(group_name)s   AS group_name
        """
        query_cluase["groupby_cluase"] = ", %(group_id)s, %(group_code)s, %(group_name)s"
        query_cluase["orderby_cluase"] = ", %(group_code)s"
        return """
            SELECT valuation.company_id {select_cluase}
            FROM stock_valuation_report valuation
                LEFT JOIN account_account account   ON account.id = valuation.stock_account_id
                LEFT JOIN product_product product   ON product.id = valuation.product_id 
                LEFT JOIN product_template template ON template.id = product.product_tmpl_id 
            WHERE {where_cluase}
            GROUP BY valuation.company_id {groupby_cluase}
            ORDER BY valuation.company_id {orderby_cluase}
        """.format_map(query_cluase)

    def _get_query_sum(self, query_cluase, offset=0, limit=None) -> str:
        def get_rounding(field):
            return "%(quantity_digits)s" if field == "quantity" else "%(value_digits)s"
        
        def query_sum_initial(field):
            return """
                COALESCE(
                    SUM(ROUND(valuation.{field}, {round_digits})) 
                    FILTER(WHERE valuation.stock_move_date < %(date_from)s)
                , 0.00)
            """.format(field=field, round_digits=get_rounding(field))
        
        def query_sum_period(field, operator):
            return """
                COALESCE(ABS(
                    SUM(ROUND(valuation.{field}, {round_digits})) 
                    FILTER(WHERE valuation.stock_move_date BETWEEN %(date_from)s AND %(date_to)s AND valuation.{field} {operator} 0)
                ), 0.00)
            """.format(field=field, round_digits=get_rounding(field), operator=operator)
        
        def query_sum_ending(field):
            return """
                COALESCE(
                    SUM(ROUND(valuation.{field}, {round_digits})) FILTER(WHERE valuation.stock_move_date <= %(date_to)s)
                , 0.00)
            """.format(field=field, round_digits=get_rounding(field))

        # prepare parameter 
        # Options offset and limit reocrds
        offset_limit = ""
        if offset:
            offset_limit += " OFFSET %(offset)s "
        if limit:
            offset_limit += " LIMIT %(limit)s "

        query = """
            WITH cte AS (
                SELECT 
                    valuation.company_id,
                    valuation.product_id,
                    %(product_code)s                                AS product_code,
                    %(product_name)s                                AS product_name,
                    %(uom_name)s                                    AS uom_name,
                    """ + query_sum_initial("quantity")      + """  AS qty_init,
                    """ + query_sum_period("quantity", ">=") + """  AS qty_plus,
                    """ + query_sum_period("quantity", "<")  + """  AS qty_minus,
                    """ + query_sum_ending("quantity")       + """  AS qty_balance,
                    """ + query_sum_initial("value")         + """  AS value_init,
                    """ + query_sum_period("value", ">=")    + """  AS value_plus,
                    """ + query_sum_period("value", "<")     + """  AS value_minus,
                    """ + query_sum_ending("value")          + """  AS value_balance
                    {select_cluase}
                FROM stock_valuation_report valuation
                    LEFT JOIN account_account account               ON account.id = valuation.stock_account_id 
                    LEFT JOIN product_product product               ON product.id = valuation.product_id 
                    LEFT JOIN product_template template             ON template.id = product.product_tmpl_id 
                    LEFT JOIN uom_uom uom                           ON uom.id = template.uom_id 
                WHERE {where_cluase}
                GROUP BY valuation.company_id, valuation.product_id, %(product_code)s, %(product_name)s, %(uom_name)s {groupby_cluase}
                ORDER BY valuation.company_id {orderby_cluase}, valuation.product_id
            )
            SELECT * FROM cte 
            WHERE (qty_init   != 0.00 OR qty_plus   != 0.00 OR qty_minus   != 0.00 OR qty_balance   != 0.00
                OR value_init != 0.00 OR value_plus != 0.00 OR value_minus != 0.00 OR value_balance != 0.00)
        """ + offset_limit
        return query.format_map(query_cluase)

    ####################################################
    # COLUMN/LINE HELPERS
    ####################################################
    def _get_calc_columns(self) -> dict[str, float]:
        return {
            "qty_init": 0.0, 
            "qty_plus": 0.0, 
            "qty_minus": 0.0, 
            "qty_balance": 0.0, 
            "value_init": 0.0, 
            "value_plus": 0.0, 
            "value_minus": 0.0, 
            "value_balance": 0.0
        }

    def _get_report_line (self, report, options, parent_line_id, line_dic):
        company_currency = self.env.company.currency_id
        quantity_digits = self.env['decimal.precision'].precision_get('Product Unit of Measure')

        columns = []
        name = line_dic["product_name"]
        for column in options["columns"]:
            column_group_key = column['column_group_key']
            expr_label = column['expression_label']

            column_value = line_dic[expr_label]
            columns.append(report._build_column_dict(column_value, column, options=options, currency=company_currency, digits=quantity_digits))

        # Report detail line 
        line = {
            'id': report._get_generic_line_id('product.product', line_dic["product_id"], parent_line_id=parent_line_id),
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
    def _report_expand_unfoldable_line_stock_valuation(self, line_dict_id, groupby, options, progress, offset, unfold_all_batch_data=None):
        lines = []
        report = self.env['account.report'].browse(options['report_id'])

        model, line_id = report._get_model_info_from_id(line_dict_id)
        expanded_line_ids = []

        if model != 'account.account':
            raise UserError(_("Wrong ID for report line to expand: %s", line_dict_id))
        
        if options.get("stock_grouping_field") != "none":
            expanded_line_ids = [line_id]

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
            lines.append(self._get_report_line(report, options, line_dict_id, res))

        return {
            'lines': lines,
            'offset_increment': report.load_more_limit,
            'has_more': has_more,
            #'progress': next_progress,
        }