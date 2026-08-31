from collections import defaultdict

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class BfBudgetSubscriptionLinkWizard(models.TransientModel):
    """Déduit le poste budgétaire d'un abonnement de ses propres factures.

    ⛔ Aucune correspondance codée en dur entre un nom de fournisseur et un
    poste : ça marcherait chez nous et nulle part ailleurs. La seule source
    fiable est ce que la comptabilité a réellement fait des factures de cet
    abonnement.

    ⚠️ L'assistant ne tranche pas les cas ambigus. Quand deux postes couvrent le
    compte dominant, ou qu'aucun ne le couvre, il le DIT et laisse le champ vide :
    un rattachement deviné au hasard fausserait un budget sans que personne ne
    puisse le retrouver.
    """

    _name = "bf.budget.subscription.link.wizard"
    _description = "Rattacher les abonnements aux postes budgétaires"

    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company
    )
    only_unassigned = fields.Boolean(
        string="Seulement ceux qui n'ont pas de poste",
        default=True,
        help="Décocher pour reconsidérer aussi les abonnements déjà rattachés.",
    )
    preview = fields.Text(string="Ce qui sera fait", compute="_compute_preview")

    def _candidates(self):
        """⚠️ En `sudo` par prudence, pas par nécessité de droits.

        Sur une installation standard, `bf_subscription` fait impliquer son
        groupe « Utilisateur » par `base.group_user` : tout utilisateur interne
        lit donc déjà les abonnements, et aucune règle d'accès n'est à ajouter
        ici. ⛔ Ne pas en redéclarer une : une liste de contrôle qui répète ce
        qu'une dépendance garantit déjà finit par mentir le jour où l'une des
        deux change.

        Le `sudo` couvre le cas où cette implication n'existerait pas, et le
        module n'écrit de toute façon qu'un seul champ : celui qu'il ajoute.
        """
        self.ensure_one()
        domain = [("company_id", "in", (self.company_id.id, False))]
        if self.only_unassigned:
            domain.append(("budget_position_id", "=", False))
        return self.env["subscription.subscription"].sudo().search(domain)

    def _dominant_account(self, subscription):
        """Le compte de charge qui porte le plus gros du réel de cet abonnement."""
        # ⚠️ `vendor_bill_ids` est un One2many vers `account.move` : le lire sans
        # `sudo` lève un AccessError chez qui n'a pas la comptabilité. Le `sudo`
        # sur la RECHERCHE ne suffisait pas, c'est la lecture du lien qui casse.
        lines = self.env["account.move.line"].sudo().search(
            [
                ("move_id", "in", subscription.sudo().vendor_bill_ids.ids),
                ("parent_state", "=", "posted"),
                ("account_id.account_type", "in",
                 ["expense", "expense_direct_cost", "expense_depreciation"]),
            ]
        )
        if not lines:
            return None
        weights = defaultdict(float)
        for line in lines:
            weights[line.account_id] += abs(line.balance)
        return max(weights.items(), key=lambda item: item[1])[0]

    def _resolve(self):
        """(rattachements, sans facture, sans poste, ambigus) — aide PURE."""
        self.ensure_one()
        Position = self.env["bf.budget.position"]
        positions = Position.search(
            [("budget_type", "=", "expense"), ("company_id", "=", self.company_id.id)]
        )
        matched, no_bill, no_position, ambiguous = [], [], [], []
        for subscription in self._candidates():
            account = self._dominant_account(subscription)
            if account is None:
                no_bill.append(subscription)
                continue
            covering = positions.filtered(lambda p, a=account: a in p.account_ids)
            if not covering:
                no_position.append((subscription, account))
            elif len(covering) > 1:
                ambiguous.append((subscription, account, covering))
            else:
                matched.append((subscription, account, covering))
        return matched, no_bill, no_position, ambiguous

    @api.depends("company_id", "only_unassigned")
    def _compute_preview(self):
        for wizard in self:
            matched, no_bill, no_position, ambiguous = wizard._resolve()
            lines = []
            for subscription, account, position in matched:
                lines.append(
                    _("✅ %(sub)s → %(position)s (compte dominant %(account)s)",
                      sub=subscription.name, position=position.display_name,
                      account=account.display_name)
                )
            for subscription in no_bill:
                lines.append(
                    _("— %(sub)s : aucune facture comptabilisée, rien à déduire",
                      sub=subscription.name)
                )
            for subscription, account in no_position:
                lines.append(
                    _("⚠️ %(sub)s : le compte %(account)s n'est dans aucun poste",
                      sub=subscription.name, account=account.display_name)
                )
            for subscription, account, covering in ambiguous:
                lines.append(
                    _("⚠️ %(sub)s : %(account)s est dans %(n)s postes, à trancher à la main",
                      sub=subscription.name, account=account.display_name, n=len(covering))
                )
            wizard.preview = "\n".join(lines) or _("Aucun abonnement à examiner.")

    def action_apply(self):
        self.ensure_one()
        matched, _no_bill, _no_position, _ambiguous = self._resolve()
        if not matched:
            raise UserError(
                _("Aucun rattachement certain à poser. Le détail est dans l'aperçu.")
            )
        for subscription, _account, position in matched:
            subscription.sudo().budget_position_id = position
        return {
            "type": "ir.actions.act_window",
            "name": _("Abonnements rattachés"),
            "res_model": "subscription.subscription",
            "view_mode": "list,form",
            "domain": [("id", "in", [s.id for s, _a, _p in matched])],
        }
