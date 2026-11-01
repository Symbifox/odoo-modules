"""Les pronoms de la signature sont ceux de la fiche.

`bf_signature_pronouns` devient un champ lié à `partner_id.bf_pronouns` : les
vues, les droits sur soi-même (`SELF_WRITEABLE_FIELDS`) et le rendu de la
signature de `bf_multi_company_email` continuent de le lire sans changer, et
ils lisent désormais la seule saisie qui existe.

La colonne de l'usager reste en base (Odoo ne retire pas une colonne dont le
champ cesse d'être stocké) : le crochet d'installation la relit une fois, puis
plus rien ne l'écrit.
"""

from odoo import fields, models


class ResUsers(models.Model):
    _inherit = "res.users"

    bf_signature_pronouns = fields.Char(
        related="partner_id.bf_pronouns",
        readonly=False,
        # Libellé distinct : `bf_preferred_name` pose déjà « Pronouns » sur
        # l'usager, et deux champs au même libellé se confondent à l'écran.
        string="Pronouns (from the contact)",
        help="Shown next to the name in the email signature. The same field "
             "as the pronouns on the person's contact.",
    )
