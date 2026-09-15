"""Le point de tournée qui porte une grille : un passage et un relevé d'un seul geste.

⚠️ La question vient AVANT le passage. Le socle défait tout ce qu'un geste touche
quand il pose une question : si le passage était noté d'abord, il serait défait avec
elle, puis noté de nouveau à l'enregistrement. Demander d'abord, écrire ensuite.

⚠️ Sans grille sur le point, rien ne change : la tournée garde son tapotement sans
question.
"""
from odoo import fields, models
from odoo.osv import expression

from odoo.addons.bf_nfc_inspection.models.bf_nfc_gesture import CHOIX_ENREGISTRER


class BfNfcRoundCheckpoint(models.Model):
    _inherit = "bf.nfc.round.checkpoint"

    checklist_id = fields.Many2one("bf.nfc.checklist", string="Grille", ondelete="restrict",
                                   help="Remplie à chaque passage : le point devient une ligne de registre.")


class BfNfcGesture(models.Model):
    _inherit = "bf.nfc.gesture"

    def _executer_round_checkpoint(self, tag, tap, params):
        point = tag._cible() if tag.res_model == "bf.nfc.round.checkpoint" else None
        grille = point.sudo().checklist_id if point else None
        if not grille:
            return super()._executer_round_checkpoint(tag, tap, params)
        self._exiger_une_personne(params)
        tournee = point.sudo().round_id
        if params.get("choix") != CHOIX_ENREGISTRER:
            return self._question_releve(
                tag, grille, params, titre="%s · %s" % (tournee.name, point.name),
                message=" · ".join(filter(None, [point.place, grille.name])))
        releve = self._enregistrer_releve(tag, grille, params, place=point.place, nom=point.name)
        resultat = super()._executer_round_checkpoint(tag, tap, dict(params, choix=None))
        resultat["message"] = "%s %s" % (releve._phrase(), resultat.get("message") or "")
        return resultat


class BfNfcTag(models.Model):
    _inherit = "bf.nfc.tag"

    def _domaine_du_guet(self):
        """Les points de tournée qui portent une grille entrent au guet, à côté des pastilles de relevé."""
        points = self.env["bf.nfc.round.checkpoint"].sudo().search([("checklist_id", "!=", False)])
        return expression.OR([super()._domaine_du_guet(), [
            ("active", "=", True), ("gesture_id.kind", "=", "round_checkpoint"),
            ("res_model", "=", "bf.nfc.round.checkpoint"), ("res_id", "in", points.ids),
        ]])

    def _grille_du_guet(self):
        self.ensure_one()
        if self.gesture_id.kind == "round_checkpoint" and self.res_model == "bf.nfc.round.checkpoint":
            return self.env["bf.nfc.round.checkpoint"].sudo().browse(self.res_id).exists().checklist_id
        return super()._grille_du_guet()
