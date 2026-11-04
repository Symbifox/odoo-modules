from datetime import datetime, time

from odoo import Command, fields

from odoo.addons.bf_budget.tests.common import BfBudgetCommon


class BfBudgetPurchaseCommon(BfBudgetCommon):
    """Reprend le décor comptable du socle et lui ajoute des achats."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.vendor = cls.env["res.partner"].create({"name": "Fournisseur commandes"})
        cls.purchase_journal = cls.env["account.journal"].create(
            {
                "name": "Achats commandes",
                "code": "BCBG",
                "type": "purchase",
                "company_id": cls.company.id,
            }
        )
        # ⚠️ Service, pas stockable : la base d'essai porte `stock`, et la
        # confirmation d'un produit stockable créerait des réceptions dont ces
        # essais n'ont que faire. `purchase_method` explicite : la facturation
        # sur la quantité COMMANDÉE, pas reçue, sinon `action_create_invoice`
        # refuse (« rien à facturer ») tant que rien n'est reçu.
        cls.product_software = cls.env["product.product"].create(
            {
                "name": "Licence annuelle",
                "type": "service",
                "purchase_ok": True,
                "purchase_method": "purchase",
                "list_price": 0.0,
            }
        )
        cls.product_software.product_tmpl_id.with_company(
            cls.company
        ).property_account_expense_id = cls.account_software
        cls.product_telecom = cls.env["product.product"].create(
            {
                "name": "Liaison télécom",
                "type": "service",
                "purchase_ok": True,
                "purchase_method": "purchase",
                "list_price": 0.0,
            }
        )
        cls.product_telecom.product_tmpl_id.with_company(
            cls.company
        ).property_account_expense_id = cls.account_telecom
        # ⚠️ Une devise inventée : sur une base nue, chercher « USD » pour tester
        # une conversion convertirait parfois une devise vers elle-même.
        cls.foreign_currency = cls.env["res.currency"].create(
            {"name": "XBP", "symbol": "¤", "rounding": 0.01}
        )
        cls.env["res.currency.rate"].create(
            {
                "name": cls.date_start,
                "currency_id": cls.foreign_currency.id,
                "company_id": cls.company.id,
                "rate": 2.0,
            }
        )
        cls.today = fields.Date.context_today(cls.env.user)

    @classmethod
    def _noon(cls, date):
        """Midi UTC : la date lue en fuseau local reste LE MÊME JOUR pour tout
        fuseau de UTC−12 à UTC+12 (une date prévue à minuit UTC, elle, change
        de jour à l'ouest de Greenwich). Limite assumée : un fuseau au-delà de
        UTC+12 (Auckland en été) lirait le lendemain."""
        return datetime.combine(date, time(12, 0))

    @classmethod
    def _po(cls, amount=100.0, qty=1.0, product=None, date=None, confirm=True,
            currency=None, analytic=None, company=None):
        """Une commande d'une ligne, confirmée par défaut."""
        company = company or cls.company
        line_vals = {
            "product_id": (product or cls.product_software).id,
            "product_qty": qty,
            "price_unit": amount,
            "date_planned": cls._noon(date or cls.today),
            "taxes_id": [Command.clear()],
        }
        if analytic:
            line_vals["analytic_distribution"] = {str(analytic.id): 100.0}
        order = cls.env["purchase.order"].with_company(company).create(
            {
                "partner_id": cls.vendor.id,
                "company_id": company.id,
                "currency_id": (currency or company.currency_id).id,
                "order_line": [Command.create(line_vals)],
            }
        )
        if confirm:
            order.button_confirm()
        return order

    @classmethod
    def _bill(cls, order, post=True, price=None, date=None):
        """La facture de la commande, par le chemin réel d'Odoo."""
        order.action_create_invoice()
        bill = order.invoice_ids.filtered(lambda move: move.state == "draft")[-1:]
        assert bill, "action_create_invoice n'a rien créé"
        bill.invoice_date = date or cls.today
        if price is not None:
            bill.invoice_line_ids.filtered("purchase_line_id").price_unit = price
        if post:
            bill.action_post()
        return bill

    @classmethod
    def _refund(cls, bill, post=True):
        """Un avoir complet de la facture, par l'extourne d'Odoo."""
        wizard = (
            cls.env["account.move.reversal"]
            .with_context(active_model="account.move", active_ids=bill.ids)
            .create({"journal_id": bill.journal_id.id, "date": cls.today})
        )
        action = wizard.refund_moves()
        refund = cls.env["account.move"].browse(action["res_id"])
        if post:
            refund.action_post()
        return refund

    def _line(self, position=None, budget=None, **extra):
        budget = budget or self.env["bf.budget"].create(
            {
                "name": "Exercice commandes",
                "company_id": self.company.id,
                "date_start": self.date_start,
                "date_end": self.date_end,
            }
        )
        vals = {
            "budget_id": budget.id,
            "position_id": (position or self.position_software).id,
            "amount_planned": 1200.0,
        }
        vals.update(extra)
        return self.env["bf.budget.line"].create(vals)
