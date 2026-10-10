/** @odoo-module **/

import { Component, useState, onWillStart } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

// Abréviations traduites au chargement (fr_CA source, en dans i18n/en.po).
function monthNames() {
    return [
        "", _t("Jan"), _t("Feb"), _t("Mar"), _t("Apr"), _t("May"), _t("Jun"),
        _t("Jul"), _t("Aug"), _t("Sep"), _t("Oct"), _t("Nov"), _t("Dec"),
    ];
}

class BudgetDashboard extends Component {
    static template = "personal_budget.Dashboard";

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");

        this.monthNames = monthNames();
        this.state = useState({
            data: null,
            bookId: null,
            availableBooks: [],
            loading: true,
            refreshing: false,
            year: null,
            availableYears: [],
            monthRange: 3,  // default: 3 most recent months
        });

        onWillStart(async () => {
            await this.loadBooks();
            await this.loadYears();
            await this.loadDashboardData();
        });
    }

    async loadBooks() {
        try {
            const books = await this.orm.call(
                "personal.budget.dashboard",
                "get_available_books",
                []
            );
            this.state.availableBooks = books;
            if (!this.state.bookId && books.length) {
                const byDefault = books.find((b) => b.is_default);
                this.state.bookId = (byDefault || books[0]).id;
            }
        } catch {
            this.notification.add(_t("Error while loading budgets"), {
                type: "danger",
            });
        }
    }

    async loadYears() {
        try {
            const years = await this.orm.call(
                "personal.budget.dashboard",
                "get_available_years",
                [this.state.bookId]
            );
            this.state.availableYears = years;
            if (years.length && !this.state.year) {
                this.state.year = new Date().getFullYear();
                if (!years.includes(this.state.year)) {
                    this.state.year = years[0];
                }
            }
        } catch {
            this.notification.add(_t("Error while loading years"), {
                type: "danger",
            });
        }
    }

    async loadDashboardData() {
        this.state.loading = !this.state.data;
        this.state.refreshing = !!this.state.data;
        try {
            const data = await this.orm.call(
                "personal.budget.dashboard",
                "get_dashboard_data",
                [this.state.year, this.state.bookId]
            );
            this.state.data = data;
        } catch {
            this.notification.add(_t("Error while loading the dashboard"), {
                type: "danger",
            });
        } finally {
            this.state.loading = false;
            this.state.refreshing = false;
        }
    }

    async onBookChange(ev) {
        this.state.bookId = parseInt(ev.target.value);
        await this.loadYears();
        await this.loadDashboardData();
    }

    async onYearChange(ev) {
        this.state.year = parseInt(ev.target.value);
        await this.loadDashboardData();
    }

    onMonthRangeChange(n) {
        this.state.monthRange = n;
    }

    async onRefresh() {
        await this.loadDashboardData();
    }

    /**
     * Return the list of month numbers (1..12) to display, based on the
     * selected range and cur_month. For a current-year dashboard with
     * cur_month=5 and range=3, returns [3, 4, 5]. For range=12 returns
     * [1..12]. For a past year, always returns the most recent N months
     * within [1..12].
     */
    visibleMonths() {
        const data = this.state.data;
        if (!data) return [];
        const cur = data.cur_month || 12;
        const range = this.state.monthRange;
        const start = Math.max(1, cur - range + 1);
        const end = Math.min(12, start + range - 1);
        const months = [];
        for (let m = start; m <= end; m++) {
            months.push(m);
        }
        return months;
    }

    monthName(num) {
        return this.monthNames[num] || "";
    }

    formatShortDate(isoDate) {
        if (!isoDate) return "";
        const parts = isoDate.split("-");
        if (parts.length !== 3) return isoDate;
        return parts[2] + " " + this.monthNames[parseInt(parts[1])];
    }

    getGapClass(pct) {
        if (!pct) return "text-muted";
        const absPct = Math.abs(pct);
        if (absPct <= 3) return "text-success";
        if (absPct <= 6) return "text-warning";
        return "text-danger fw-bold";
    }

    getOverallOffsetClass(pct) {
        const absPct = Math.abs(pct);
        if (absPct <= 3) return "bg-success";
        if (absPct <= 6) return "bg-warning text-dark";
        return "bg-danger";
    }

    /**
     * Color a single month cell based on real vs plan delta.
     * For expense: real > plan → red, real < plan → green.
     * For revenue: real > plan → green, real < plan → red.
     */
    getMonthCellClass(row, month, categoryType) {
        const real = row.by_month[month] || 0;
        const plan = row.plan_by_month ? (row.plan_by_month[month] || 0) : 0;
        if (!plan) return "";
        const delta = real - plan;
        const tolerance = Math.abs(plan) * 0.05;  // 5% tolerance
        if (Math.abs(delta) <= tolerance) return "";
        const overspending = categoryType === "expense" ? delta > 0 : delta < 0;
        return overspending ? "text-danger" : "text-success";
    }

    formatCurrency(value) {
        if (value === null || value === undefined) return "0,00 $";
        const num = parseFloat(value);
        const formatted = Math.abs(num)
            .toFixed(2)
            .replace(/\B(?=(\d{3})+(?!\d))/g, " ")
            .replace(".", ",");
        return (num < 0 ? "-" : "") + formatted + " $";
    }

    formatCurrencyShort(value) {
        if (!value) return "";
        const num = parseFloat(value);
        const formatted = Math.abs(num)
            .toFixed(0)
            .replace(/\B(?=(\d{3})+(?!\d))/g, " ");
        return (num < 0 ? "-" : "") + formatted + " $";
    }

    formatGap(value) {
        if (value === null || value === undefined || value === 0) return "";
        const num = parseFloat(value);
        const formatted = Math.abs(num)
            .toFixed(0)
            .replace(/\B(?=(\d{3})+(?!\d))/g, " ");
        return (num > 0 ? "+" : "-") + formatted + " $";
    }

    formatDeltaPct(value) {
        if (value === null || value === undefined) return "";
        const num = parseFloat(value);
        return (num >= 0 ? "+" : "") + num.toFixed(1) + "%";
    }

    async viewTransactions(categoryId, year, categoryType) {
        const action = await this.orm.call(
            "personal.budget.dashboard",
            "action_view_transactions",
            [categoryId, year, categoryType, this.state.bookId]
        );
        await this.action.doAction(action);
    }

    async viewTransactionForm(transactionId) {
        const action = await this.orm.call(
            "personal.budget.dashboard",
            "action_view_transaction_form",
            [transactionId]
        );
        await this.action.doAction(action);
    }

    async viewLoans() {
        const action = await this.orm.call(
            "personal.budget.dashboard",
            "action_view_loans",
            [this.state.bookId]
        );
        await this.action.doAction(action);
    }

    async viewInvoices() {
        const action = await this.orm.call(
            "personal.budget.dashboard",
            "action_view_invoices",
            [this.state.bookId]
        );
        await this.action.doAction(action);
    }

    async viewRecurring() {
        const action = await this.orm.call(
            "personal.budget.dashboard",
            "action_view_recurring",
            [this.state.bookId]
        );
        await this.action.doAction(action);
    }

    async onQuickAddExpense() {
        await this.action.doAction(
            "personal_budget.action_quick_add_expense",
            {
                onClose: async (closeInfo) => {
                    if (!closeInfo || !closeInfo.special) {
                        await this.loadDashboardData();
                    }
                },
            }
        );
    }
}

registry.category("actions").add("budget_dashboard", BudgetDashboard);
