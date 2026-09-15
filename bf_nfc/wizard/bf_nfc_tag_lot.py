"""Créer d'un coup une pastille par fiche : les douze points d'une ronde, les dix portables.

⚠️ Une fiche qui a DÉJÀ une pastille en service pour ce geste est sautée, et
l'écran dit combien. Relancer l'assistant sur la même sélection ne double donc
rien : c'est le cas courant, on ajoute trois équipements et on resélectionne tout.

⚠️ Les pastilles naissent avant la gravure, jamais l'inverse, comme dans
l'application. L'assistant rend la liste des pastilles créées, d'où s'impriment
les étiquettes QR ; la gravure se fait ensuite depuis « Mes pastilles ».
"""
import json

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError


class BfNfcTagLot(models.TransientModel):
    _name = "bf.nfc.tag.lot"
    _description = "Création de pastilles en lot"

    res_model = fields.Char(required=True, readonly=True)
    res_ids = fields.Char(required=True, readonly=True)
    fiche_count = fields.Integer(string="Fiches", compute="_compute_resume")
    deja_count = fields.Integer(string="Déjà munies", compute="_compute_resume")
    gesture_id = fields.Many2one(
        "bf.nfc.gesture", string="Geste", required=True,
        domain="[('kind', '!=', 'menu'), '|', ('target_model', '=', False), "
               "('target_model', '=', res_model)]",
    )
    prefixe = fields.Char(
        string="Préfixe du libellé",
        help="Ajouté devant le nom de chaque fiche : « Ronde du soir · » donne "
             "« Ronde du soir · Porte arrière ».")
    params = fields.Char(string="Paramètres", help="JSON facultatif, le même pour toutes.")
    confirm_required = fields.Boolean(string="Confirmer avant d'agir", default=True)

    @api.model
    def default_get(self, champs):
        valeurs = super().default_get(champs)
        if "gesture_id" in champs and not valeurs.get("gesture_id") and valeurs.get("res_model"):
            geste = self.env["bf.nfc.gesture"].sudo().search(
                [("target_model", "=", valeurs["res_model"]), ("kind", "!=", "menu")],
                limit=1)
            valeurs["gesture_id"] = geste.id or False
        return valeurs

    def _fiches(self):
        self.ensure_one()
        if self.res_model not in self.env:
            raise UserError(_("Ce type de fiche n'existe pas sur ce système."))
        ids = [int(i) for i in (self.res_ids or "").split(",") if i.strip().isdigit()]
        return self.env[self.res_model].browse(ids).exists()

    def _deja_munies(self, fiches):
        if not self.gesture_id:
            return fiches.browse()
        pris = set(self.env["bf.nfc.tag"].sudo().search([
            ("res_model", "=", fiches._name), ("res_id", "in", fiches.ids),
            ("gesture_id", "=", self.gesture_id.id),
        ]).mapped("res_id"))
        return fiches.filtered(lambda f: f.id in pris)

    @api.depends("res_model", "res_ids", "gesture_id")
    def _compute_resume(self):
        for lot in self:
            fiches = lot._fiches() if lot.res_model in self.env else self.env["res.partner"].browse()
            lot.fiche_count = len(fiches)
            lot.deja_count = len(lot._deja_munies(fiches)) if fiches else 0

    def action_creer(self):
        self.ensure_one()
        if not self.env.user.has_group("bf_nfc.group_nfc_manager"):
            raise AccessError(_("Créer des pastilles est réservé à la gestion."))
        if self.params:
            try:
                if not isinstance(json.loads(self.params), dict):
                    raise ValueError
            except ValueError:
                raise UserError(_("Les paramètres doivent être un objet JSON."))
        fiches = self._fiches()
        attendu = self.gesture_id.target_model
        if attendu and attendu != fiches._name:
            raise UserError(_("Le geste « %s » ne vise pas ce type de fiche.", self.gesture_id.name))
        a_faire = fiches - self._deja_munies(fiches)
        if not a_faire:
            raise UserError(_("Toutes ces fiches ont déjà une pastille pour ce geste."))
        Tag = self.env["bf.nfc.tag"]
        crees = Tag.browse()
        for fiche in a_faire:
            nom = fiche.display_name
            valeurs = {
                "name": ("%s · %s" % (self.prefixe.strip(), nom)) if self.prefixe else nom,
                "gesture_id": self.gesture_id.id,
                "res_model": fiche._name,
                "res_id": fiche.id,
                "confirm_required": self.confirm_required,
            }
            if "place" in fiche._fields and fiche.place:
                valeurs["place"] = fiche.place
            if self.params:
                valeurs["params"] = self.params
            crees |= Tag.create(valeurs)
        return {
            "type": "ir.actions.act_window",
            "name": _("%s pastilles créées", len(crees)),
            "res_model": "bf.nfc.tag",
            "view_mode": "list,form",
            "domain": [("id", "in", crees.ids)],
        }
