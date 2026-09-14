"""Le sas : là où une réponse attend d'être détachée de son jeton.

Une réponse écrite directement dans le registre définitif serait anonyme par
ses colonnes et bavarde par son `id` : les identifiants montent dans l'ordre
des insertions. Qui observe le registre des invitations deux fois de suite
apprend qui a répondu entre les deux passages, et les réponses arrivées dans
la même fenêtre sont les siennes.

Le sas casse cet ordre. Les réponses y attendent, puis une passe les verse en
lot et **dans un ordre tiré au hasard**, en jetant le jeton. Le lot n'est versé
qu'une fois le seuil de répondants atteint, ou à la fermeture de la vague.

Aucun rôle n'a de droit sur ce modèle : il ne se lit qu'en `sudo`, depuis le
contrôleur public et depuis la passe de versement.
"""

from odoo import fields, models


class PulseStaging(models.Model):
    _name = "bf.ex.pulse.staging"
    _description = "Réponse de pulse en attente de versement"
    _inherit = ["bf.ex.pulse.sans.journal"]
    _log_access = False
    _order = "id"

    campaign_id = fields.Many2one(
        "bf.ex.pulse.campaign", string="Vague", required=True,
        ondelete="cascade", index=True,
    )
    question_id = fields.Many2one(
        "bf.ex.pulse.question", string="Question", required=True,
        ondelete="cascade",
    )
    token = fields.Char(
        string="Jeton", required=True, index=True,
        help="Le lien avec la personne. Détruit au versement.",
    )
    segment_key = fields.Char(string="Segment")
    value_scale = fields.Integer(string="Note")
    value_text = fields.Text(string="Commentaire")
