"""Le registre des réponses : quoi, sans qui.

🔴 Invariant du module : aucune colonne de ce modèle ne mène à une personne.
Pas de `res.users`, pas de `res.partner`, pas de `hr.employee`, et pas les
colonnes automatiques d'Odoo, retirées par `_log_access = False`. Un essai le
vérifie en parcourant les champs déclarés plutôt qu'en relisant le fichier.

La date est celle de la vague, jamais celle du geste. Une réponse arrivée le
mardi à 14 h 02 se range sous la vague, comme les autres.

Aucun rôle n'a de droit de lecture ici. La seule porte est l'agrégat, qui
applique les seuils. Laisser l'administration lire cette table reviendrait à
publier les verbatims d'une équipe de deux personnes.
"""

from odoo import fields, models


class PulseAnswer(models.Model):
    _name = "bf.ex.pulse.answer"
    _description = "Réponse de pulse (sans répondant)"
    _inherit = ["bf.ex.pulse.sans.journal"]
    _log_access = False
    _order = "id"

    campaign_id = fields.Many2one(
        "bf.ex.pulse.campaign", string="Vague", required=True,
        ondelete="cascade", index=True,
    )
    company_id = fields.Many2one(
        "res.company", string="Société", required=True, index=True,
    )
    question_id = fields.Many2one(
        "bf.ex.pulse.question", string="Question", required=True,
        ondelete="restrict",
    )
    metric_id = fields.Many2one(
        "bf.ex.pulse.metric", string="Axe", required=True, ondelete="restrict",
        help="Copié au versement. Une question déplacée d'un axe à l'autre ne "
             "réécrit pas les scores déjà publiés.",
    )
    segment_key = fields.Char(string="Segment", index=True)
    period = fields.Date(
        string="Période", required=True, index=True,
        help="La date d'ouverture de la vague, pas celle de la réponse.",
    )
    value_scale = fields.Integer(string="Note")
    value_text = fields.Text(string="Commentaire")
