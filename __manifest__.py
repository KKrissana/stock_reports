# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

{
    'name': 'Stock Reports',
    'description': """
Stock Reports
=================
    """,
    'author': 'KKrissana',
    'category': 'Inventory/Inventory',
    'website': 'https://github.com/KKrissana',
    'sequence': 35,
    'depends': ['stock', 'stock_account', 'account','account_reports'],
    'data': [
        'security/stock_report_security.xml',
        'security/ir.model.access.csv',

        'views/account_report_views.xml',
        'views/stock_valuation_report_views.xml',

        'data/stock_aged_report.xml',
        'data/stock_incoming_report.xml',
        'data/stock_outgoing_report.xml',
        'data/stock_valuation_report.xml',
        'data/stock_report_actions.xml',
        'data/menu.xml',
    ],
    'assets': {
        'web.assets_backend': [
           'stock_reports/static/src/components/**/*',
       ],
    },
    'demo': [
        
    ],
    'license': 'LGPL-3',
    'auto_install': True,
}
