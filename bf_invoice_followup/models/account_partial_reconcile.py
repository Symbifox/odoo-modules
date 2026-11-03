from odoo import api, models


class AccountPartialReconcile(models.Model):
    """Trace du chemin qui NE marche PAS, gardée pour qu'on ne le reprenne pas.

    ⚠️ Mesuré le 2026-11-02 sur un banc : quand un paiement est enregistré par
    le compositeur, Odoo 18 appelle bien ``account.partial.reconcile.create``,
    mais **avec une liste vide** — les lettrages réels naissent ailleurs. Une
    surcharge ici ne voit donc jamais la facture, et le ``payment_state`` lu à
    cet instant porte encore la valeur d'avant.

    La clôture des relances se fait donc au **balayage quotidien**
    (``_bf_close_paid_followups``, appelé par les deux tâches planifiées), et
    c'est ce que le README promet.
    """

    _inherit = "account.partial.reconcile"

    @api.model_create_multi
    def create(self, vals_list):
        return super().create(vals_list)
