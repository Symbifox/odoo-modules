"""Les gabarits : ce qu'on pose d'habitude, prêt à poser.

Un gabarit est une RECETTE. Une ligne par pastille (« Extincteur ; Hall ») et un
clic : il crée ce qu'il faut. La recette de base crée une pastille par ligne, avec
son geste, ses paramètres et son menu. Un satellite ajoute la sienne : une tournée
et ses points, des équipements et leur pastille de prêt, des salles.

🔴 **Ce qu'un gabarit pose se fixe sur la pastille, jamais au tapotement.** Les
paramètres et la grille d'un gabarit deviennent des champs et des paramètres de
pastille, que la personne qui tape ne peut pas remplacer (cf. ``_params``).

⚠️ Les gabarits livrés sont en ``noupdate`` : un client les adapte, et une mise à
jour du module ne doit pas écraser ce qu'il a changé.
"""
import json

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

SECTEURS = [
    ("incendie", "Fire safety"),
    ("sst", "Occupational health and safety"),
    ("bureau", "Office"),
    ("immeuble", "Building"),
    ("garde", "Childcare services"),
    ("autre", "Other"),
]


class BfNfcTemplate(models.Model):
    _name = "bf.nfc.template"
    _description = "Tag template"
    _order = "secteur, sequence, name"

    name = fields.Char(string="Template", required=True, translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    secteur = fields.Selection(SECTEURS, string="Sector", required=True, default="autre")
    description = fields.Text(translate=True)
    reference = fields.Char(string="Reference", translate=True,
                            help="The obligation or standard this "
                                 "template helps prove.")
    recette = fields.Selection(
        [("pastilles", "One tag per line")], string="What it creates",
        required=True, default="pastilles")
    gesture_id = fields.Many2one("bf.nfc.gesture", string="Gesture", ondelete="cascade",
                                 domain=[("kind", "!=", "menu")])
    gesture_kind = fields.Selection(related="gesture_id.kind")
    params = fields.Char(string="Parameters", help="JSON set on each tag "
                                                   "created.")
    confirm_required = fields.Boolean(string="Confirm before acting", default=True)
    choice_ids = fields.One2many("bf.nfc.template.choice", "template_id", string="Menu", copy=True)
    cible_requise = fields.Boolean(
        string="Requires a target record",
        help="All tags created target the same record, chosen when the "
             "template is applied: the building for a report, the company "
             "for a printer.")
    signee_recommandee = fields.Boolean(
        string="Signed tag recommended",
        help="The people tapping have no account: the tag should be an "
             "NTAG 424 DNA, with its identifier entered after engraving.")
    exemple = fields.Text(string="Example lines", translate=True)

    @api.constrains("recette", "gesture_id", "choice_ids")
    def _check_geste(self):
        for gabarit in self:
            if gabarit.recette == "pastilles" and not (gabarit.gesture_id or gabarit.choice_ids):
                raise ValidationError(_("“%s”: a tag needs a gesture or a "
                                        "menu.",
                                        gabarit.name))

    def action_utiliser(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.name,
            "res_model": "bf.nfc.template.apply",
            "view_mode": "form",
            "target": "new",
            "context": {"default_template_id": self.id},
        }

    # ------------------------------------------------------------------
    # Les recettes
    # ------------------------------------------------------------------
    def _appliquer(self, pose, lignes):
        """Répartit vers ``_recette_<recette>`` et rend les pastilles créées."""
        self.ensure_one()
        methode = getattr(self, "_recette_%s" % self.recette, None)
        if methode is None:
            raise UserError(_("The recipe “%s” is not installed on this "
                              "system.", self.recette))
        return methode(pose, lignes)

    def _valeurs_pastille(self, pose, nom, endroit):
        """Les valeurs d'une pastille créée par ce gabarit. Un satellite ajoute les siennes (la grille)."""
        self.ensure_one()
        valeurs = {
            "name": ("%s · %s" % (pose.prefixe.strip(), nom)) if pose.prefixe else nom,
            "place": endroit or False,
            "gesture_id": self.gesture_id.id if self.gesture_id else self.env.ref("bf_nfc.gesture_menu").id,
            "confirm_required": self.confirm_required,
        }
        try:
            params = json.loads(self.params) if self.params else {}
        except ValueError:
            raise UserError(_("The parameters of template “%s” are not "
                              "valid JSON.", self.name))
        if not isinstance(params, dict):
            raise UserError(_("The parameters of template “%s” must be a "
                              "JSON object.", self.name))
        if pose.adresse:
            params["url"] = pose.adresse.strip()
        if params:
            valeurs["params"] = json.dumps(params, ensure_ascii=False)
        if pose.cible:
            valeurs.update({"res_model": pose.cible._name, "res_id": pose.cible.id})
        if self.choice_ids:
            # Un gabarit à menu pose une pastille « menu », quel que soit son geste.
            valeurs["gesture_id"] = self.env.ref("bf_nfc.gesture_menu").id
            valeurs["choice_ids"] = [(0, 0, {
                "name": c.name, "gesture_id": c.gesture_id.id, "params": c.params,
                "style": c.style, "sequence": c.sequence}) for c in self.choice_ids]
        return valeurs

    def _recette_pastilles(self, pose, lignes):
        Tag = self.env["bf.nfc.tag"]
        return Tag.concat(*[Tag.create(self._valeurs_pastille(pose, nom, endroit))
                            for nom, endroit in lignes])


class BfNfcTemplateChoice(models.Model):
    _name = "bf.nfc.template.choice"
    _description = "Template menu choice"
    _order = "sequence, id"

    template_id = fields.Many2one("bf.nfc.template", required=True, ondelete="cascade", index=True)
    sequence = fields.Integer(default=10)
    name = fields.Char(string="Button label", required=True, translate=True)
    gesture_id = fields.Many2one("bf.nfc.gesture", string="Gesture", required=True, ondelete="cascade",
                                 domain=[("kind", "!=", "menu")])
    params = fields.Char(string="Parameters")
    style = fields.Selection(
        [("principal", "Primary"), ("secondaire", "Secondary"), ("danger", "Warning")],
        default="secondaire", required=True)
