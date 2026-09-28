"""Le geste d'une étiquette vierge : elle n'a encore rien à faire.

⚠️ Un geste plutôt qu'une pastille sans geste : ``gesture_id`` est obligatoire
dans le socle, et c'est lui que l'application, le journal et les filtres lisent.
« Vierge » y devient un état qu'on voit, pas un champ vide qu'on devine.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class BfNfcGesture(models.Model):
    _inherit = "bf.nfc.gesture"

    kind = fields.Selection(
        selection_add=[("vierge", "Étiquette à associer")],
        ondelete={"vierge": "cascade"},
    )

    @api.model
    def _search(self, domain, *args, **kwargs):
        # Posé par les routes de l'application mobile : jamais graver « vierge ».
        if self.env.context.get("bf_qr_sans_vierges"):
            domain = [("kind", "!=", "vierge")] + list(domain or [])
        return super()._search(domain, *args, **kwargs)

    def _executer_vierge(self, tag, tap, params):
        """Mène à l'association qui a le droit de la faire, refuse poliment les autres.

        N'écrit rien : l'association est un geste de la fiche, avec son propre
        formulaire, pas un effet de bord du scan.
        """
        self.ensure_one()
        if tag._peut_associer():
            return {
                "titre": tag.name,
                "message": _("Étiquette à associer."),
                "url": "/odoo/bf.nfc.tag/%s" % tag.id,
            }
        raise UserError(_("Cette étiquette n'est pas encore associée."))
