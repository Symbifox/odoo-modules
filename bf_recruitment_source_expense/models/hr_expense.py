# Part of bf_recruitment_source_expense. Voir LICENSE.
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class HrExpense(models.Model):
    """On ne paie pas un poste, on paie un site pour un affichage sur un poste."""

    _inherit = "hr.expense"

    recruitment_source_id = fields.Many2one(
        "hr.recruitment.source", string="Site d'emploi",
        index="btree_not_null", ondelete="set null",
        help="Le site qui a facturé cet affichage. Sert au coût par "
             "candidature de ce site. Un débours de recrutement qui n'est pas "
             "un affichage (un déplacement, un repas) n'en a pas.",
    )

    @api.onchange("recruitment_source_id")
    def _onchange_recruitment_source_id(self):
        """Choisir le site remplit le poste : le site en porte déjà un.

        Saisir les deux à la main, c'est se donner deux occasions de se
        tromper pour une seule information.
        """
        for expense in self:
            if expense.recruitment_source_id:
                expense.job_id = expense.recruitment_source_id.job_id

    @api.constrains("recruitment_source_id", "job_id")
    def _check_source_belongs_to_the_job(self):
        """Le garde que l'`onchange` ne peut pas poser.

        ⚠️ Un `onchange` ne tourne que dans un formulaire. Une dépense créée
        par import, par RPC ou par un autre module ne le déclenche pas : sans
        cette contrainte, un débours pourrait s'imputer au site d'un poste et
        au poste d'un autre, et les deux totaux mentiraient en sens contraire.
        """
        for expense in self:
            source = expense.recruitment_source_id
            if source and expense.job_id and source.job_id != expense.job_id:
                # 🔴 Le repère ne s'appelle PAS `source`. La fonction de
                # traduction du socle a `source` pour PREMIER paramètre, qui
                # est le texte lui-même, et un `source=` en mot-clé lève un
                # « got multiple values for argument 'source' », loin d'ici,
                # au moment de valider l'écriture.
                raise ValidationError(_(
                    "Le site « %(site)s » affiche le poste "
                    "« %(site_job)s », pas « %(job)s ». Un débours ne peut "
                    "pas s'imputer au site d'un poste et au poste d'un autre.",
                    site=source.source_id.display_name,
                    site_job=source.job_id.display_name,
                    job=expense.job_id.display_name,
                ))
