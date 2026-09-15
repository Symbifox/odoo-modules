"""Poser un gabarit : une ligne par pastille, et un clic.

⚠️ Les lignes s'écrivent « nom ; endroit », l'endroit facultatif. Une ligne vide ou
un commentaire (« # … ») est sauté. Au-delà de 200 lignes on refuse : un gabarit se
pose par étage ou par bâtiment, pas l'inventaire d'un parc en un clic.
"""
import json

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

MAX_LIGNES = 200


class BfNfcTemplateApply(models.TransientModel):
    _name = "bf.nfc.template.apply"
    _description = "Pose d'un gabarit de pastilles"

    template_id = fields.Many2one("bf.nfc.template", string="Gabarit", required=True, readonly=True)
    description = fields.Text(related="template_id.description")
    reference = fields.Char(related="template_id.reference")
    recette = fields.Selection(related="template_id.recette")
    cible_requise = fields.Boolean(related="template_id.cible_requise")
    signee_recommandee = fields.Boolean(related="template_id.signee_recommandee")
    demande_adresse = fields.Boolean(compute="_compute_demande_adresse")
    lignes = fields.Text(string="Une ligne par pastille", required=True,
                         help="« nom ; endroit », l'endroit est facultatif.")
    prefixe = fields.Char(string="Préfixe du libellé")
    cible = fields.Reference(selection="_selection_cible", string="Fiche visée")
    adresse = fields.Char(string="Adresse à ouvrir", help="https://…")
    responsible_id = fields.Many2one("res.users", string="Responsable",
                                     default=lambda self: self.env.user,
                                     help="Reçoit les alertes de ce qui est créé.")

    @api.model
    def _selection_cible(self):
        return self.env["bf.nfc.tag"]._selection_cible()

    @api.model
    def default_get(self, champs):
        valeurs = super().default_get(champs)
        gabarit = self.env["bf.nfc.template"].browse(valeurs.get("template_id"))
        if gabarit.exists() and "lignes" in champs and not valeurs.get("lignes"):
            valeurs["lignes"] = gabarit.exemple or False
        return valeurs

    @api.depends("template_id")
    def _compute_demande_adresse(self):
        """⚠️ Lu comme du JSON, pas comme du texte : « url » dans {"urgence": 1} n'est
        pas une adresse, et l'écran cessait alors de la demander."""
        for pose in self:
            gabarit = pose.template_id
            try:
                deja = "url" in (json.loads(gabarit.params) if gabarit.params else {})
            except ValueError:
                deja = False
            pose.demande_adresse = gabarit.recette == "pastilles" \
                and gabarit.gesture_id.kind == "url" and not deja

    def _lignes(self):
        self.ensure_one()
        lues = []
        for brute in (self.lignes or "").splitlines():
            brute = brute.strip()
            if not brute or brute.startswith("#"):
                continue
            nom, _sep, endroit = brute.partition(";")
            if nom.strip():
                lues.append((nom.strip()[:120], endroit.strip()[:120]))
        if not lues:
            raise UserError(_("Écrivez au moins une ligne : une par pastille à créer."))
        if len(lues) > MAX_LIGNES:
            raise UserError(_("%s lignes : posez ce gabarit par étage ou par bâtiment, "
                              "pas plus de %s d'un coup.", len(lues), MAX_LIGNES))
        return lues

    def action_appliquer(self):
        self.ensure_one()
        if not self.env.user.has_group("bf_nfc.group_nfc_manager"):
            raise AccessError(_("Poser un gabarit est réservé à la gestion des pastilles."))
        if self.cible_requise and not self.cible:
            raise UserError(_("Ce gabarit demande la fiche que les pastilles visent."))
        if self.demande_adresse and not (self.adresse or "").startswith(("https://", "http://")):
            raise UserError(_("Donnez l'adresse que la pastille ouvrira (https://…)."))
        crees = self.template_id._appliquer(self, self._lignes())
        return {
            "type": "ir.actions.act_window",
            "name": _("%(n)s pastilles : %(gabarit)s", n=len(crees), gabarit=self.template_id.name),
            "res_model": "bf.nfc.tag",
            "view_mode": "list,form",
            "domain": [("id", "in", crees.ids)],
        }
