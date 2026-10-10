/** @odoo-module **/
import { _t } from "@web/core/l10n/translation";
import { formatFloat } from "@web/core/utils/numbers";
import { patch } from "@web/core/utils/patch";
import { MyRoute } from "@bf_route/ma_route/ma_route";

/** Half away from zero, as Odoo rounds, with a nudge for binary fractions (0.125 → 0.13). */
function roundCents(value) {
    const sign = value < 0 ? -1 : 1;
    return (sign * Math.round(Math.abs(value) * 100 + 1e-7)) / 100;
}

/**
 * Sales on the road, in "My route": at each stop with products, the quantities
 * delivered, the empties taken back, an estimate of the amount, and how it was paid.
 * The estimate uses the unit prices WITH taxes sent by the server; the invoice made
 * by the server is the reference, and a payment within 5 cents of it pays it exactly.
 */
patch(MyRoute.prototype, {
    setup() {
        super.setup(...arguments);
        this.state.sales = {};
    },

    async load() {
        await super.load(...arguments);
        for (const day of this.state.days) {
            for (const stop of day.stops) {
                if (stop.sale) {
                    this.saleForm(stop);
                }
            }
        }
    },

    saleForm(stop) {
        if (!this.state.sales[stop.id]) {
            const lines = {};
            for (const line of stop.sale.lines) {
                lines[line.product_id] = { delivered: line.planned, empties: line.planned };
            }
            // No method by default: a "cash" chosen for the worker would record money never received.
            this.state.sales[stop.id] = { lines, mode: null, amount: "", reference: "" };
        }
        return this.state.sales[stop.id];
    },

    /**
     * The invoice's sum, line by line: each line rounded to the cent, each tax of each line
     * rounded to the cent (Odoo's "round per line"). Falls back on the unit price with taxes
     * when the server says the taxes are not simple percentages.
     */
    /** The price for the quantity delivered: the last threshold of the price list reached. */
    priceAt(line, quantity) {
        const breaks = line.price_breaks || [[1, line.price, line.unit_total]];
        let found = breaks[0];
        for (const br of breaks) {
            if (Math.abs(quantity) >= br[0]) {
                found = br;
            }
        }
        return { price: found[1], unitTotal: found[2] };
    },

    lineTotal(quantity, price, rates, unitTotal) {
        if (!quantity) {
            return 0;
        }
        if (!rates) {
            return quantity * unitTotal;
        }
        const base = roundCents(quantity * price);
        return rates.reduce((total, rate) => total + roundCents((base * rate) / 100), base);
    },

    estimate(stop) {
        const form = this.saleForm(stop);
        let total = 0;
        for (const line of stop.sale.lines) {
            const entry = form.lines[line.product_id];
            const delivered = Number(entry.delivered) || 0;
            const empties = Number(entry.empties) || 0;
            const priced = this.priceAt(line, delivered);
            total += this.lineTotal(delivered, priced.price, line.tax_rates, priced.unitTotal);
            if (line.has_deposit) {
                // Two invoice lines: the deposit on the full ones, the credit on the empties.
                total += this.lineTotal(delivered, line.deposit_price, line.deposit_tax_rates,
                    line.deposit_unit_total);
                total += this.lineTotal(-empties, line.deposit_price, line.deposit_tax_rates,
                    line.deposit_unit_total);
            }
        }
        return roundCents(total);
    },

    /** In the person's language: 34,00 in French, 34.00 in English. */
    money(value) {
        return formatFloat(Number(value) || 0, { digits: [16, 2] });
    },

    extraFor(stop, newState) {
        const extra = super.extraFor(stop, newState);
        if (!stop.sale || newState !== "done") {
            return extra;
        }
        const form = this.saleForm(stop);
        if (!form.mode) {
            this.notification.add(_t("Choose how the customer paid (or On account)."), { type: "warning" });
            return false;
        }
        const amount = form.amount === "" ? Math.max(this.estimate(stop), 0) : Number(form.amount);
        if (Number.isNaN(amount) || amount < 0) {
            this.notification.add(_t("The amount received must be a number."), { type: "danger" });
            return false;
        }
        if (form.mode === "card" && amount > 0 && !form.reference.trim()) {
            this.notification.add(_t("Enter the authorization number shown by the terminal."), {
                type: "warning",
            });
            return false;
        }
        return {
            ...extra,
            sale: {
                lines: stop.sale.lines.map((line) => ({
                    product_id: line.product_id,
                    delivered: Number(form.lines[line.product_id].delivered) || 0,
                    empties: line.has_deposit ? Number(form.lines[line.product_id].empties) || 0 : 0,
                })),
                payment: {
                    mode: form.mode,
                    amount: form.mode === "account" ? 0 : amount,
                    reference: form.reference.trim(),
                },
            },
        };
    },

    /** A refused sale comes back on screen as it was entered. */
    restoreItem(stop, item) {
        super.restoreItem(stop, item);
        const sale = item.extra && item.extra.sale;
        if (sale && stop.sale) {
            const form = this.saleForm(stop);
            for (const line of sale.lines) {
                if (form.lines[line.product_id]) {
                    form.lines[line.product_id].delivered = line.delivered;
                    form.lines[line.product_id].empties = line.empties;
                }
            }
            form.mode = sale.payment.mode;
            form.amount = String(sale.payment.amount ?? "");
            form.reference = sale.payment.reference || "";
        }
    },

    payModeLabel(mode) {
        return {
            cash: _t("Cash"),
            cheque: _t("Cheque"),
            card: _t("Card (terminal)"),
            account: _t("On account"),
        }[mode];
    },
});
