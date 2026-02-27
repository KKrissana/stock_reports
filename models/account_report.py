# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models, _
from odoo.tools import format_date, SQL, Query
from odoo.exceptions import UserError

from collections import defaultdict
from datetime import datetime, time, timedelta

class AccountReport(models.Model):
    _inherit = 'account.report'

    filter_product = fields.Boolean(
        string="Products",
        compute=lambda x: x._compute_report_option_filter('filter_product'), 
        readonly=False, 
        store=True, 
        depends=['root_report_id', 'section_main_report_ids'],
    )

    filter_picking = fields.Boolean(
        string="Pickings",
        compute=lambda x: x._compute_report_option_filter('filter_picking'), 
        readonly=False, 
        store=True, 
        depends=['root_report_id', 'section_main_report_ids'],
    )

    ####################################################
    # OPTIONS: CORE INHERIT
    ####################################################

    def _get_options_domain(self, options, date_scope):
        self.ensure_one()
        domain = super()._get_options_domain(options, date_scope)
        domain += self._get_options_product_domain(options)
        domain += self._get_options_picking_domain(options)
        return domain

    ####################################################
    # OPTIONS: PRODUCTS FILTER
    ####################################################

    def _init_options_product(self, options, previous_options):
        if not self.filter_product:
            return
        
        options['product'] = True
        previous_product_ids = previous_options.get('product_ids') or []
        selected_product_ids = [int(product) for product in previous_product_ids]
        # search instead of browse so that record rules apply and filter out the ones the user does not have access to
        selected_products = selected_product_ids and self.env['product.product'].with_context(active_test=False).search([('id', 'in', selected_product_ids)]) or self.env['product.product']
        options['selected_product_ids'] = selected_products.mapped('name')
        options['product_ids'] = selected_products.ids

        options['product_categories'] = previous_options.get('product_categories') or []
        selected_product_category_ids = [int(category) for category in options['product_categories']]
        selected_product_categories = selected_product_category_ids and self.env['product.category'].browse(selected_product_category_ids) or self.env['product.category']
        options['selected_product_categories'] = selected_product_categories.mapped('name')

    def _get_options_product(self, options):
        product_ids, product_category_ids = [], []
        if options.get('product_ids'):
            product_ids = [int(product) for product in options['product_ids']]
        if options.get('product_categories'):
            product_category_ids = [int(category) for category in options['product_categories']]
        return product_ids, product_category_ids

    @api.model
    def _get_options_product_domain(self, options):
        domain = []
        product_ids, product_category_ids = self._get_options_product(options)
        if product_ids:
            domain.append(("product_id", "in", product_ids))
        if product_category_ids:
            domain.append(("product_id.categ_id", "in", product_category_ids))
        return domain


    ####################################################
    # OPTIONS: PICKING FILTER
    ####################################################
    def _init_options_picking(self, options, previous_options):
        if not self.filter_picking:
            return
        
        options['picking'] = True
        previous_picking_ids = previous_options.get('picking_ids') or []
        selected_picking_ids = [int(picking) for picking in previous_picking_ids]
        # search instead of browse so that record rules apply and filter out the ones the user does not have access to
        selected_pickings = selected_picking_ids and self.env['stock.picking'].with_context(active_test=False).search([('id', 'in', selected_picking_ids)]) or self.env['stock.picking']
        options['selected_picking_ids'] = selected_pickings.mapped('name')
        options['picking_ids'] = selected_pickings.ids
    
    def _get_options_picking(self, options):
        picking_ids = []
        if options.get('picking_ids'): 
            picking_ids = [int(picking) for picking in options['picking_ids']]
        return picking_ids

    @api.model
    def _get_options_picking_domain(self, options):
        picking_ids = self._get_options_picking(options)
        return [("move_id.stock_move_id.picking_id", "in", picking_ids)] if picking_ids else []
    
    ####################################################
    # CARET OPTIONS MANAGEMENT
    ####################################################
    def open_product(self, options, params):
        report = self.env['account.report'].browse(options['report_id'])
        res_id = report._get_res_id_from_line_id(params.get('line_id'), 'product.product')
        if not res_id:
            raise UserError(_("Wrong ID for report line to open: %s", params.get('line_id')))

        product = self.env['product.product'].browse(res_id)
        view_id = self.env.ref('product.product_normal_form_view').id
        return {
            'name': product.display_name,
            'type': 'ir.actions.act_window',
            'res_model': 'product.product',
            'view_mode': 'form',
            'view_id': view_id,
            'views': [(view_id, 'form')],
            'res_id': product.id,
        }
    
    def open_picking(self, options, params):
        self.env['stock.picking'].check_access_rights('read')
        report = self.env['account.report'].browse(options['report_id'])
        markup = report._get_markup(params.get('line_id'))
        if not markup and not markup.get("stock.picking"):
            raise UserError(_("Wrong ID for report line to open: %s", params.get('line_id')))
        
        picking = self.env['stock.picking'].browse(markup.get("stock.picking"))
        view_id = self.env.ref('stock.view_picking_form').id
        return {
            'name': picking.display_name,
            'type': 'ir.actions.act_window',
            'res_model': 'stock.picking',
            'view_mode': 'form',
            'view_id': view_id,
            'views': [(view_id, 'form')],
            'res_id': picking.id,
        }
    
    def open_stock_move(self, options, params):
        self.env['stock.move'].check_access_rights('read')
        report = self.env['account.report'].browse(options['report_id'])
        markup = report._get_markup(params.get('line_id'))
        if not markup and not markup.get("stock.move"):
            raise UserError(_("Wrong ID for report line to open: %s", params.get('line_id')))
        
        move = self.env['stock.move'].browse(markup.get('stock.move'))
        view_id = self.env.ref('stock.view_move_form').id
        return {
            'name': '%s, %s' % (move.reference, move.display_name),
            'type': 'ir.actions.act_window',
            'res_model': 'stock.move',
            'view_mode': 'form',
            'view_id': view_id,
            'views': [(view_id, 'form')],
            'res_id': move.id,
        }
    
    def open_order_line(self, options, params=None):
        report = self.env['account.report'].browse(options['report_id'])
        markup = report._get_markup(params.get('line_id'))
        view_id = False
        order_line_id = False
        order_line_model = ""
        if markup.get("purchase.order.line"):
            order_line_model = "purchase.order.line"
            order_line_id = markup.get("purchase.order.line")
            view_id = self.env.ref('purchase.purchase_order_line_form2').id
        elif markup.get("sale.order.line"):
            order_line_model = "sale.order.line"
            order_line_id = markup.get("sale.order.line")
            view_id = self.env.ref('sale.sale_order_line_view_form_readonly').id
        
        if not order_line_id:
            raise UserError(_("Wrong ID for report line to open: %s", params.get('line_id')))
        
        self.env[order_line_model].check_access_rights('read')
        order_line = self.env[order_line_model].browse(order_line_id)
        return {
            'name': '%s, %s' % (order_line.order_id.display_name, order_line.product_id.display_name),
            'type': 'ir.actions.act_window',
            'res_model': order_line_model,
            'view_mode': 'form',
            'view_id': view_id,
            'views': [(view_id, 'form')],
            'res_id': order_line.id,
        }
    
    
    def open_valuation(self, options, params):
        date_to = options['date']['date_to'] + ' ' + str(time.max.strftime("%H:%M:%S.%f"))
        line_id = params.get('line_id')
        line_dic = self._get_res_ids_from_line_id(line_id, ['product.product', 'account.account'])
        
        domain = [("stock_move_date", "<=", date_to)]
        product_id = line_dic['product.product']
        if not product_id:
            raise UserError(_("Wrong product id for report line to open: %s", line_id))
        
        account_id = line_dic['account.account']
        if account_id is None:
            raise UserError(_("Wrong account id for report line to open: %s", line_id))
        
        domain = [("product_id", "=", product_id), ("stock_move_date", "<=", date_to)]
        if options.get("stock_grouping_field") != "none":
            domain.append(("stock_account_id", "=", account_id if account_id > 0 else False))

        return {
            'name': 'Valuation',
            'type': 'ir.actions.act_window',
            'res_model': 'stock.valuation.report',
            'view_mode': 'list,form',
            'views': [
                (self.env.ref("stock_reports.stock_valuation_report_list").id, 'list'), 
                (self.env.ref("stock_reports.stock_valuation_report_form").id, 'form')
            ],
            'domain': domain,
        }