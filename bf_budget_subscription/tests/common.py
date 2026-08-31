from odoo import Command, fields

from odoo.addons.bf_budget.tests.common import BfBudgetCommon


class BfBudgetSubscriptionCommon(BfBudgetCommon):
    """Reprend le décor comptable du socle et lui ajoute des abonnements."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.vendor = cls.env["res.partner"].create({"name": "Fournisseur récurrent"})
        # ⚠️ Sur une base nue, la devise de la société est USD : chercher « USD »
        # pour tester une conversion revient à convertir une devise vers
        # elle-même, et le test passe en ne prouvant rien.
        cls.foreign_currency = cls.env["res.currency"].create(
            {"name": "XBG", "symbol": "₿", "rounding": 0.01}
        )
        cls.env["res.currency.rate"].create(
            {
                "name": cls.date_start,
                "currency_id": cls.foreign_currency.id,
                "company_id": cls.company.id,
                "rate": 2.0,
            }
        )
        cls.purchase_journal = cls.env["account.journal"].create(
            {
                "name": "Achats budget",
                "code": "ACHBG",
                "type": "purchase",
                "company_id": cls.company.id,
            }
        )

    _code_counter = 0

    @classmethod
    def _make_subscription(cls, cycle="monthly", amount=100.0, start=None, **extra):
        # ⚠️ Le code est posé À LA MAIN, et ce n'est pas une commodité.
        # `bf_subscription` le tire de `ir.sequence`, dont l'enregistrement porte
        # `company_id = 1` (le défaut d'`ir.sequence`, appliqué au chargement des
        # données). Sous toute AUTRE société, `next_by_code` ne trouve rien, rend
        # False, et le module retombe sur le littéral « Nouveau » — que la
        # contrainte d'unicité (code, société) refuse au deuxième enregistrement.
        # Le décor de ce satellite ne doit pas dépendre de ce défaut-là.
        cls._code_counter += 1
        vals = {
            "code": extra.pop("code", "TEST-BUD-%04d" % cls._code_counter),
            "name": extra.pop("name", "Abonnement %s" % cycle),
            "company_id": cls.company.id,
            "currency_id": extra.pop("currency_id", cls.currency.id),
            "vendor_id": cls.vendor.id,
            "category": "software_saas",
            "state": extra.pop("state", "active"),
            "cycle": cycle,
            "cycle_amount": amount,
            "start_date": start or cls.date_start,
        }
        vals.update(extra)
        return cls.env["subscription.subscription"].create(vals)

    @classmethod
    def _vendor_bill(cls, subscription, account, amount, date):
        """Une vraie facture fournisseur rattachée à l'abonnement.

        Sert à alimenter `last_vendor_bill_date`, qui est calculé et stocké : on
        ne peut pas l'écrire à la main, et c'est tant mieux.
        """
        move = cls.env["account.move"].create(
            {
                "move_type": "in_invoice",
                "partner_id": cls.vendor.id,
                "journal_id": cls.purchase_journal.id,
                "invoice_date": date,
                "date": date,
                "company_id": cls.company.id,
                "subscription_id": subscription.id,
                "invoice_line_ids": [
                    Command.create(
                        {
                            "name": "Abonnement",
                            "quantity": 1,
                            "price_unit": amount,
                            "account_id": account.id,
                            "tax_ids": [Command.clear()],
                        }
                    )
                ],
            }
        )
        move.action_post()
        return move
