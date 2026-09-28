"""L'association au premier scan : ce que l'étiquette fera désormais.

Trois choix couvrent presque tout : ouvrir une fiche, ouvrir une adresse,
consigner un passage. La gestion peut choisir n'importe quel autre geste du
catalogue ; les groupes de la société, ceux qui ne sont pas réservés.
"""
from odoo import _, api, fields, models


class BfQrAssocier(models.TransientModel):
    _name = "bf.qr.associer"
    _description = "Associer une étiquette QR"

    tag_id = fields.Many2one("bf.nfc.tag", string="Étiquette", required=True, readonly=True)
    reference = fields.Char(related="tag_id.qr_reference", string="N° d'étiquette")
    gesture_id = fields.Many2one(
        "bf.nfc.gesture", string="Ce qu'elle fait", required=True,
        default=lambda self: self.env.ref("bf_nfc.gesture_open", raise_if_not_found=False),
        domain=lambda self: self._domaine_gestes())
    gesture_kind = fields.Selection(related="gesture_id.kind")
    gesture_writes = fields.Boolean(related="gesture_id.writes")
    cible = fields.Reference(selection="_selection_cible", string="Fiche")
    url = fields.Char(string="Adresse", help="https://…")
    name = fields.Char(string="Libellé", help="Vide : le nom de la fiche.")
    place = fields.Char(string="Posée sur", help="Où l'étiquette est collée.")
    qr_public = fields.Boolean(
        string="S'ouvre sans compte",
        help="Un visiteur sans compte arrive directement à l'adresse, ou à la page publique "
             "de la fiche si elle en a une et qu'elle est publiée.")

    @api.model
    def _selection_cible(self):
        gestion = self.env.user.has_group("bf_nfc.group_nfc_manager")
        noms = self.env["bf.nfc.tag"]._modeles_cibles(gestion=gestion)
        IrModel = self.env["ir.model"].sudo()
        return [(nom, IrModel._get(nom).name or nom) for nom in noms]

    @api.model
    def _domaine_gestes(self):
        """Les gestes proposés : jamais « vierge », et pas les réservés hors gestion.

        ⚠️ Un domaine évalué pour la personne, pas un champ calculé : le calcul
        n'était fait que sur le formulaire neuf, et l'assistant enregistré le
        rendait vide. Le contrôle qui compte reste celui de ``_associer``.
        """
        domaine = [("kind", "!=", "vierge")]
        if not self.env.user.has_group("bf_nfc.group_nfc_manager"):
            domaine.append(("reserve_gestion", "=", False))
        return domaine

    @api.onchange("gesture_id")
    def _onchange_gesture_id(self):
        if self.gesture_id.kind == "url":
            self.qr_public = True
        if self.gesture_id.writes:
            self.qr_public = False

    def action_associer(self):
        self.ensure_one()
        self.tag_id._associer(self.gesture_id, cible=self.cible or None, url=self.url,
                              libelle=self.name, place=self.place, public=self.qr_public)
        return {
            "type": "ir.actions.act_window",
            "res_model": "bf.nfc.tag",
            "res_id": self.tag_id.id,
            "view_mode": "form",
            "target": "current",
        }
