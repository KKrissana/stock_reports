/** @odoo-module */

import { _t } from "@web/core/l10n/translation";
import { WarningDialog } from "@web/core/errors/error_dialogs";
import { AccountReport } from "@account_reports/components/account_report/account_report";
import { AccountReportFilters } from "@account_reports/components/account_report/filters/filters";

export class AgedStockFilters extends AccountReportFilters {
    static template = "stock_reports.AgedStockFilters";

    //------------------------------------------------------------------------------------------------------------------
    // Aging Stock Interval
    //------------------------------------------------------------------------------------------------------------------
    async setAgingStockInterval(ev) {
        const agedStockInterval = parseInt(ev.target.value);
        if (agedStockInterval < 1) {
            this.dialog.add(WarningDialog, {
                title: _t("Odoo Warning"),
                message: _t("Intervals cannot be smaller than 1"),
            });
            return;
        }

        await this.filterClicked({ optionKey:"aged_stock_interval", optionValue: agedStockInterval, reload: true });
    }

}

AccountReport.registerCustomComponent(AgedStockFilters);


