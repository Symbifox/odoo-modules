"""Les questions que le module pose, reconnaissables autrement que par leur nom.

🔴 La première version lisait le LIBELLÉ du champ d'accueil pour savoir laquelle
des deux questions on regardait (« est-ce que "représenté" est dans le texte? »).
Ça tient tant que personne ne traduit la question, ne la reformule, ni n'en
ajoute une qui contient le même mot. Autrement dit : ça ne tient pas. Le rôle
est donc porté par un champ technique, et le libellé redevient ce qu'il doit
être, du texte pour les humains.
"""

from odoo import fields, models


class AppointmentIntakeField(models.Model):
    _inherit = "appointment.intake.field"

    bf_visit_role = fields.Selection(
        [
            ("representation", "Représentation par un courtier"),
            ("broker_name", "Nom du courtier du visiteur"),
        ],
        string="Rôle dans le registre des visites",
        help="Posé par le module de visites. Dit où la réponse se range dans "
             "le registre, quel que soit le libellé de la question.",
    )
