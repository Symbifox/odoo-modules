from odoo import fields, models

#: Ce que fait Gen d'une société qu'il a complétée. « Proposer »
#: est le défaut : un modèle de langage qui lit des extraits du web se trompe
#: d'entité sur une chaîne à succursales, et rien ne s'écrit sans un regard.
GEN_COMPANY_MODES = [
    ("propose", "Propose the values to me (I confirm)"),
    ("fill", "Fill in the empty fields"),
    ("off", "Do nothing on creation"),
]


class ResUsers(models.Model):
    _inherit = "res.users"

    bf_gen_company_mode = fields.Selection(
        GEN_COMPANY_MODES,
        string="Companies completed by Gen",
        default="propose",
        required=True,
        help="When an incomplete company is created, Gen searches online "
             "for its website, phone and address. “Propose” waits for "
             "your confirmation; “Fill in” writes the empty fields when "
             "confidence is high enough; “Do nothing” keeps only the "
             "“Complete with Gen” button.",
    )

    @property
    def SELF_READABLE_FIELDS(self):
        return super().SELF_READABLE_FIELDS + ["bf_gen_company_mode"]

    @property
    def SELF_WRITEABLE_FIELDS(self):
        return super().SELF_WRITEABLE_FIELDS + ["bf_gen_company_mode"]
