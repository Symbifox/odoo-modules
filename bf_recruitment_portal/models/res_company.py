# Part of bf_recruitment_portal. Voir LICENSE.
from odoo import fields, models


class ResCompany(models.Model):
    """Les deux interrupteurs que le locataire tient lui-même.

    ⚠️ Sur la société et non en paramètre d'instance : un groupe qui recrute
    dans deux sociétés n'a pas forcément le même régime des deux côtés, et le
    portail sert des dossiers qui appartiennent à une société précise. C'est
    aussi là que `bf_recruitment_expense` a posé son taux de repli.
    """

    _inherit = "res.company"

    recruitment_portal_book_enabled = fields.Boolean(
        string="Cahier d'entrevues téléchargeable",
        default=True,
        help="Quand c'est décoché, la personne évaluée voit toujours la "
             "décision et le motif écrit, mais ne peut plus télécharger le "
             "cahier. Le droit d'accès reste, il s'exerce alors sur demande.",
    )
    recruitment_portal_otp_required = fields.Boolean(
        string="Code à usage unique sur le portail candidat",
        default=False,
        help="Un code de six chiffres est envoyé à l'adresse du dossier avant "
             "d'ouvrir la page. ⚠️ Le lien arrive lui aussi par courriel : le "
             "code ne protège donc pas d'une boîte compromise, il protège d'un "
             "lien qui a fuité (transféré, laissé dans un historique, lu "
             "par-dessus une épaule).",
    )
