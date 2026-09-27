"""L'état des impayés d'une fraction.

Art. 1069 C.c.Q. : « Celui qui, par quelque mode que ce soit, [...] acquiert une
fraction de copropriété divise est tenu au paiement, avec les intérêts, de
toutes les charges communes dues relativement à cette fraction au moment de
l'acquisition. » L'impayé se compte donc PAR FRACTION, et il voyage avec elle.
C'est pourquoi l'état vit ici et non sur la fiche du copropriétaire : celui qui
vend ne l'emporte pas.

⚠️ L'alinéa 2 du même article ouvre un délai que le module ne suit pas encore :
le proposant acquéreur peut demander au syndicat un état des charges dues, et il
n'en est tenu « que si l'état lui est fourni par le syndicat dans les 15 jours
de la demande ». Un syndicat qui laisse filer ces quinze jours perd sa créance
contre l'acquéreur. Le calcul ci-dessous en est la matière première ; le suivi
de la demande, du préavis au propriétaire et de l'échéance appartient à une
tâche distincte, comme l'attestation de l'art. 1068.1 et ses quinze jours à
elle.
"""
from odoo import api, fields, models


class BfPropertyUnit(models.Model):
    _inherit = "bf.property.unit"

    call_line_ids = fields.One2many(
        "bf.property.fund.call.line", "unit_id", string="Contributions"
    )
    currency_id = fields.Many2one(
        related="organisation_id.company_id.currency_id", string="Devise"
    )
    # 🔴 **Réservé à la gestion, et voici pourquoi.**
    # `bf_property_portal` ouvre la fraction au groupe Portail sous une règle à
    # deux branches : la fraction POSSÉDÉE, ou la fraction OCCUPÉE lorsqu'elle
    # est louée. La seconde branche existe pour que le locataire retrouve son
    # logement à l'écran — l'art. 1070 al. 1 C.c.Q. le porte au registre par son
    # nom et son adresse.
    #
    # Mais une ACL de lecture porte sur le MODÈLE, pas sur ce que le gabarit
    # affiche. Mesuré : un utilisateur Portail inscrit comme occupant
    # lisait `overdue_total = 1 287,50 $` et `overdue_days = 94` sur la fraction
    # qu'il loue — ce que SON PROPRIÉTAIRE doit au syndicat. Rien à l'art. 1070
    # n'ouvre cela au locataire : le registre l'y inscrit, il ne le lui ouvre
    # pas.
    #
    # ⚠️ Le calcul traverse `call_line_ids`, dont l'ACL est fermée au portail —
    # et cela ne protégeait rien, parce que les cinq champs sont STOCKÉS : un
    # stocké se lit à la colonne, sans retraverser sa dépendance. Une ACL fermée
    # en amont ne ferme pas un champ stocké en aval.
    #
    # ⚠️ Le portail n'affiche ces montants nulle part, ni au copropriétaire ni à
    # l'occupant. Les restreindre ne retire donc rien à personne, et c'est la
    # règle du module : rien du registre n'atteint le portail par effet de bord.
    overdue_amount = fields.Monetary(
        string="Capital en souffrance",
        currency_field="currency_id",
        compute="_compute_overdue",
        store=True,
        groups="bf_property_core.group_bf_property_user",
    )
    overdue_interest = fields.Monetary(
        string="Intérêts en souffrance",
        currency_field="currency_id",
        compute="_compute_overdue",
        store=True,
        groups="bf_property_core.group_bf_property_user",
    )
    overdue_total = fields.Monetary(
        string="Total en souffrance",
        currency_field="currency_id",
        compute="_compute_overdue",
        store=True,
        groups="bf_property_core.group_bf_property_user",
    )
    overdue_since = fields.Date(
        string="En souffrance depuis",
        compute="_compute_overdue",
        store=True,
        help="Échéance de la plus ancienne contribution non soldée. C'est de "
             "cette date que se comptent les trois mois de l'art. 1094 C.c.Q. "
             "et, séparément, les 30 jours de l'art. 2729.",
        groups="bf_property_core.group_bf_property_user",
    )
    overdue_days = fields.Integer(
        string="Jours de retard", compute="_compute_overdue", store=True,
        groups="bf_property_core.group_bf_property_user",
    )

    @api.depends(
        "call_line_ids.balance",
        "call_line_ids.interest_balance",
        "call_line_ids.is_overdue",
        "call_line_ids.call_id.due_date",
    )
    def _compute_overdue(self):
        today = fields.Date.context_today(self)
        for unit in self:
            late = unit.call_line_ids.filtered("is_overdue")
            unit.overdue_amount = sum(late.mapped("balance"))
            unit.overdue_interest = sum(late.mapped("interest_balance"))
            unit.overdue_total = unit.overdue_amount + unit.overdue_interest
            dates = [
                line.call_id.due_date for line in late if line.call_id.due_date
            ]
            unit.overdue_since = min(dates) if dates else False
            unit.overdue_days = (today - min(dates)).days if dates else 0

    def _charges_due(self):
        """L'état des charges communes dues, au sens de l'art. 1069 al. 2.

        Rend le détail contribution par contribution, capital et intérêts
        séparés. L'alinéa 3 veut que l'état « fourni » soit ajusté selon le
        dernier budget annuel : cet ajustement suppose un jugement sur
        l'exercice en cours et ne se fait pas ici.
        """
        self.ensure_one()
        rows = []
        for line in self.call_line_ids.filtered(
            lambda l: l.call_id.state in ("issued", "closed") and l.total_due > 0
        ):
            rows.append(
                {
                    "call": line.call_id.name,
                    "due_date": line.call_id.due_date,
                    "capital": line.balance,
                    "interest": line.interest_balance,
                    "total": line.total_due,
                }
            )
        return sorted(rows, key=lambda row: (row["due_date"] or fields.Date.today()))
