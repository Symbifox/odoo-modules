# Part of bf_recruitment_letter. Voir LICENSE.
from odoo import api, fields, models


class LetterDocument(models.Model):
    """La lettre sait de quelle candidature elle parle.

    🔴 C'est la seule façon qu'un gabarit atteigne la candidature. `bf_letter_writer`
    rend la fusion **contre la lettre elle-même** :

        self._render_template(str(body), "letter.document", [self.id], ...)

    Un gabarit qui écrirait ``{{ object.job_id }}`` ne trouverait donc rien : le
    `object` de la fusion EST la lettre. Avec ce champ, il écrit
    ``{{ object.applicant_id.job_id.name }}`` et les gabarits restent des données
    qu'on modifie dans l'interface, sans code et sans déploiement.
    """

    _inherit = "letter.document"

    applicant_id = fields.Many2one(
        "hr.applicant", string="Candidature",
        index=True, ondelete="set null",
        help="La candidature que cette lettre concerne. Les champs de fusion la "
             "lisent : nom, poste, conditions proposées.",
    )

    @api.onchange("applicant_id")
    def _onchange_applicant_id(self):
        """Reprendre le destinataire du dossier plutôt que le faire retaper.

        ⚠️ L'identité vit sur `hr.candidate`, pas sur la candidature : en
        Odoo 18, `partner_id` et `email_from` sont des *related* portés par la
        personne, et `partner_name` est calculé. On passe donc par la
        candidature, qui les expose, sans jamais recopier une valeur en base :
        la lettre ne garde que ce que la personne qui rédige y laisse.
        """
        for letter in self:
            applicant = letter.applicant_id
            if not applicant:
                continue
            if applicant.company_id:
                letter.company_id = applicant.company_id
            if applicant.partner_id:
                letter.partner_id = applicant.partner_id
            elif applicant.partner_name:
                letter.recipient_name = applicant.partner_name

    # ------------------------------------------------------------------
    # La palette de fusion autorisée
    # ------------------------------------------------------------------

    # 🔴 Sans cette surcharge, AUCUN gabarit d'offre utile ne se rend.
    #
    # `mail.render.mixin` refuse à qui n'a pas `mail.group_mail_template_editor`
    # toute expression absente de `mail_allowed_qweb_expressions()`. Le coeur
    # n'en autorise que sept (`object.name`, `object.partner_id`,
    # `object.user_id` et leurs variantes), et la comparaison se fait sur la
    # chaîne ENTIÈRE, telle quelle. Un recruteur ordinaire qui applique le
    # gabarit se prend alors « Only members of Mail Template Editor group are
    # allowed to edit templates containing sensible placeholders ».
    #
    # ⚠️ Les deux mauvaises portes de sortie : donner
    # `mail.group_mail_template_editor` aux recruteurs revient à leur donner
    # l'écriture de QWeb arbitraire partout ; rendre en `sudo()` désarme la
    # garde pour tout le monde. On élargit donc la liste, ici seulement, à une
    # palette NOMMÉE, et rien d'autre ne passe.
    #
    # ⚠️ Conséquence pour le client : un gabarit réécrit avec un champ hors de
    # cette palette lèvera chez un recruteur ordinaire. C'est le prix de ne pas
    # ouvrir l'écriture de gabarits, et le message d'erreur nomme le groupe.
    _RECRUITMENT_MERGE_PALETTE = (
        "object.company_id.name",
        "object.recipient_name",
        "object.applicant_id.partner_name",
        "object.applicant_id.job_id.name",
        "object.applicant_id.department_id.name",
        "object.applicant_id.job_id.contract_type_id.name",
        "object.applicant_id.salary_proposed_display",
        "object.applicant_id.offer_conditions_display",
        "object.applicant_id.offer_availability_display",
    )

    def mail_allowed_qweb_expressions(self):
        return super().mail_allowed_qweb_expressions() + self._RECRUITMENT_MERGE_PALETTE
