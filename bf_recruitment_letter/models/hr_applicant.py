# Part of bf_recruitment_letter. Voir LICENSE.
from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import format_amount

OFFER_TEMPLATE = "bf_recruitment_letter.letter_template_job_offer"


class HrApplicant(models.Model):
    _inherit = "hr.applicant"

    letter_ids = fields.One2many(
        "letter.document", "applicant_id", string="Lettres",
    )
    letter_count = fields.Integer(
        string="Nombre de lettres", compute="_compute_letter_count",
    )

    # 🔴 `groups` OBLIGATOIRE, et c'est la même règle que partout ici : le
    # coeur réserve `salary_proposed` à `hr_recruitment.group_hr_recruitment_user`.
    # Un champ dérivé sans restriction rendrait par la bande ce que le coeur
    # protège : `bf_recruitment_expense` a payé exactement cette leçon sur le coût du panel.
    salary_proposed_display = fields.Char(
        string="Salaire proposé (mis en forme)",
        compute="_compute_salary_proposed_display",
        groups="hr_recruitment.group_hr_recruitment_user",
        help="Le salaire proposé, écrit comme il se lit dans une lettre. "
             "`salary_proposed` est un flottant : rendu tel quel, un gabarit "
             "écrirait « 65000.0 » dans une offre d'emploi.",
    )

    offer_conditions_display = fields.Char(
        string="Conditions particulières (mises en forme)",
        compute="_compute_offer_displays",
        groups="hr_recruitment.group_hr_recruitment_user",
    )
    offer_availability_display = fields.Char(
        string="Entrée en fonction (mise en forme)",
        compute="_compute_offer_displays",
    )

    @api.depends("salary_proposed", "company_id")
    def _compute_salary_proposed_display(self):
        """Le montant, écrit comme il se lit, et son absence, dite.

        🔴 Le repli vit DANS le champ, jamais dans le gabarit, et ce n'est pas
        un choix de style. Le coeur n'autorise à qui n'est pas éditeur de
        gabarits que des expressions comparées **telles quelles** à une liste
        blanche (`mail_allowed_qweb_expressions`). Un
        ``{{ object.x or "à préciser" }}`` ne correspond à aucune entrée et
        fait lever le rendu chez tout recruteur ordinaire. Les champs de
        fusion doivent donc rester des chemins nus.
        """
        for applicant in self:
            company = applicant.company_id or applicant.env.company
            applicant.salary_proposed_display = (
                format_amount(applicant.env, applicant.salary_proposed, company.currency_id)
                if applicant.salary_proposed else _("à préciser")
            )

    @api.depends("salary_proposed_extra", "candidate_id.availability")
    def _compute_offer_displays(self):
        for applicant in self:
            applicant.offer_conditions_display = (
                (applicant.salary_proposed_extra or "").strip() or _("aucune")
            )
            disponibilite = applicant.candidate_id.availability
            applicant.offer_availability_display = (
                fields.Date.to_string(disponibilite) if disponibilite
                else _("à convenir ensemble")
            )

    @api.depends("letter_ids")
    def _compute_letter_count(self):
        # ⚠️ `sudo` : `letter.document` n'est pas lisible par un membre de
        # panel. Sans ça, le compteur du bouton dépendrait de qui regarde.
        counts = dict(self.env["letter.document"].sudo()._read_group(
            domain=[("applicant_id", "in", self.ids)],
            groupby=["applicant_id"],
            aggregates=["__count"],
        ))
        for applicant in self:
            applicant.letter_count = counts.get(applicant, 0)

    # ------------------------------------------------------------------
    # L'offre
    # ------------------------------------------------------------------

    def _check_can_offer(self):
        """Les trois refus qui valent mieux qu'une lettre produite quand même."""
        self.ensure_one()
        # ⚠️ `letter.document.partner_id` est REQUIS. Sans ce garde, une
        # candidature sans contact fait remonter une violation de contrainte
        # SQL brute à l'écran, qui ne dit ni ce qui manque ni où le poser.
        # On ne crée pas le contact à la place : fabriquer un `res.partner`
        # dans le dos de qui recrute pose un porteur de renseignements
        # personnels de plus, avec son propre régime de conservation.
        if not self.partner_id:
            raise UserError(_(
                "La candidature de %(who)s n'a pas de contact. Une lettre "
                "s'adresse à une personne inscrite au carnet d'adresses : "
                "posez le contact sur la fiche du candidat, puis reprenez.",
                who=self.partner_name or self.display_name,
            ))
        if self.refuse_reason_id:
            raise UserError(_(
                "La candidature de %(who)s a été refusée. Rédiger une offre "
                "d'emploi par-dessus un refus consigné laisserait deux "
                "décisions contradictoires au même dossier. Rouvrez la "
                "candidature d'abord, si c'est bien l'intention.",
                who=self.partner_name or self.display_name,
            ))
        # 🔴 Le même principe que le coût par embauche de `bf_recruitment_expense` : un document
        # qui ne sait pas se taire sur ce qui lui manque ment. Une offre sans
        # aucune condition annoncerait un salaire de zéro, en toutes lettres.
        if not self.salary_proposed and not (self.salary_proposed_extra or "").strip():
            raise UserError(_(
                "Aucune condition n'est consignée pour %(who)s : le salaire "
                "proposé est à zéro et les conditions particulières sont "
                "vides. L'offre annoncerait donc un salaire de zéro. Posez au "
                "moins l'un des deux sur la candidature.",
                who=self.partner_name or self.display_name,
            ))

    def action_draft_offer_letter(self):
        """Créer l'offre depuis le dossier, et l'ouvrir pour être relue.

        La lettre est un BROUILLON : le gabarit pose la structure et les
        conditions déjà consignées, la personne qui recrute écrit le reste et
        la relit. Rien ne part d'ici.
        """
        self.ensure_one()
        self._check_can_offer()
        template = self.env.ref(OFFER_TEMPLATE, raise_if_not_found=False)
        if not template:
            raise UserError(_(
                "Le gabarit « Offre d'emploi » est introuvable. Il est livré "
                "avec ce module et n'est jamais récrit par une mise à niveau : "
                "s'il a été supprimé, il faut le recréer à la main."
            ))
        letter = self.env["letter.document"].create({
            "applicant_id": self.id,
            "company_id": self.company_id.id or self.env.company.id,
            "partner_id": self.partner_id.id,
            "recipient_name": self.partner_name or False,
            "template_id": template.id,
        })
        letter.action_apply_template()
        return {
            "type": "ir.actions.act_window",
            "name": _("Offre d'emploi"),
            "res_model": "letter.document",
            "res_id": letter.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_view_letters(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Lettres"),
            "res_model": "letter.document",
            "view_mode": "list,form",
            "domain": [("applicant_id", "=", self.id)],
            "context": {"default_applicant_id": self.id},
        }
