"""Connecteur : la voie par laquelle un ordre atteint un appareil.

Le lot 1 n'en livre qu'un, le connecteur manuel : l'ordre devient une
activité au responsable du point d'accès. Un connecteur matériel (2N, Seam…)
s'ajoute par un module qui étend `kind` et fournit `_push_<kind>(order)`.

🔴 Principe : la serrure ne dépend jamais d'Odoo pour s'ouvrir. L'appareil
valide ses identifiants lui-même ; Odoo ne fait qu'envoyer des ordres
d'ajout et de retrait, en différé, rejouables. Une panne d'Odoo retarde un
ajout ou un retrait, elle ne bloque jamais une porte.
"""
from odoo import _, fields, models
from odoo.exceptions import UserError


class BfPropertyAccessConnector(models.Model):
    _name = "bf.property.access.connector"
    _description = "Connecteur d'accès"
    _order = "name"

    name = fields.Char(string="Nom", required=True)
    active = fields.Boolean(default=True)
    kind = fields.Selection(
        [("manual", "Manuel : activité au responsable")],
        string="Type",
        required=True,
        default="manual",
    )
    company_id = fields.Many2one(
        "res.company", string="Société", default=lambda self: self.env.company
    )
    max_attempts = fields.Integer(
        string="Essais avant de passer la main",
        default=3,
        help="Après ce nombre d'échecs, l'ordre devient une activité au "
             "responsable du point d'accès, qui le fait à la main.",
    )
    note = fields.Text(string="Note")

    def _bf_is_manual(self):
        return not self or self.kind == "manual"

    def _bf_push(self, order):
        """Envoie un ordre à l'appareil.

        Rend (fait, message, référence externe). Lève si le type n'a pas
        d'implémentation : c'est une erreur de configuration, pas un échec
        d'appareil.
        """
        self.ensure_one()
        method = getattr(self, "_push_%s" % self.kind, None)
        if method is None:
            raise UserError(
                _("Le connecteur %(name)s (type %(kind)s) n'a pas d'envoi.")
                % {"name": self.name, "kind": self.kind}
            )
        return method(order)
