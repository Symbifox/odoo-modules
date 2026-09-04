# Part of bf_recruitment_letter. Voir LICENSE.
"""Ce qu'on prouve ici.

1. L'offre se rédige depuis la candidature, et la fusion aboutit VRAIMENT :
   le nom, le poste et les conditions sont dans le corps, sans jeton résiduel.
2. Le montant est mis en forme. Rendu brut, un gabarit écrirait « 65000.0 »
   dans une offre d'emploi.
3. Le bouton REFUSE de produire une lettre qui mentirait : ni offre sans
   aucune condition, ni offre par-dessus un refus consigné.
4. 🔴 La lettre ne fait pas fuir le salaire. `letter.document` est lisible par
   tout employé ; le coeur réserve `salary_proposed` aux recruteurs. La paire
   le prouve : l'employé ordinaire est refusé sur l'offre, le recruteur est
   servi, et l'employé garde accès aux lettres ordinaires.
"""

from odoo.exceptions import AccessError, UserError
from odoo.tools.rendering_tools import parse_inline_template
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestRecruitmentLetter(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.ref("base.main_company")
        cls.job = cls.env["hr.job"].create({
            "name": "Ébéniste", "company_id": cls.company.id,
        })
        cls.recruiter = cls._make_user("offre_recruteur", [
            "base.group_user", "hr_recruitment.group_hr_recruitment_user"])
        cls.plain = cls._make_user("offre_employe", ["base.group_user"])

    @classmethod
    def _make_user(cls, login, groups):
        return cls.env["res.users"].create({
            "name": login, "login": login, "email": login + "@example.invalid",
            "company_id": cls.company.id,
            "groups_id": [(6, 0, [cls.env.ref(g).id for g in groups])],
        })

    def _make_applicant(self, salary=68000.0, extra=False, name="Fannie Aubut",
                        with_partner=True):
        # ⚠️ Le coeur CRÉE le contact tout seul dès qu'il y a un courriel :
        # `hr.candidate._inverse_partner_email` appelle `find_or_create`. Pour
        # obtenir une candidature réellement sans contact (un CV papier saisi
        # à la main), il faut donc n'avoir NI contact NI adresse.
        candidate = self.env["hr.candidate"].create({
            "partner_name": name,
            "email_from": "fannie@example.invalid" if with_partner else False,
            "company_id": self.company.id, "availability": "2026-10-05",
        })
        return self.env["hr.applicant"].create({
            "candidate_id": candidate.id, "job_id": self.job.id,
            "company_id": self.company.id,
            "salary_proposed": salary,
            "salary_proposed_extra": extra or False,
        })

    # ------------------------------------------------------------------
    # 1. La fusion aboutit
    # ------------------------------------------------------------------

    def test_the_offer_is_drafted_and_the_merge_resolves(self):
        applicant = self._make_applicant()
        action = applicant.with_user(self.recruiter).action_draft_offer_letter()
        letter = self.env["letter.document"].browse(action["res_id"])

        self.assertEqual(letter.applicant_id, applicant)
        body = str(letter.body_html)
        self.assertIn("Ébéniste", body, "Le poste n'a pas été fusionné.")
        self.assertIn(
            "2026-10-05", body,
            "La disponibilité vit sur hr.candidate : elle n'a pas été atteinte.",
        )
        self.assertNotIn(
            "{{", body,
            "Un champ de fusion est resté tel quel dans le corps de la lettre.",
        )

    def test_the_letter_knows_its_recipient(self):
        applicant = self._make_applicant()
        action = applicant.with_user(self.recruiter).action_draft_offer_letter()
        letter = self.env["letter.document"].browse(action["res_id"])
        self.assertEqual(letter.recipient_name, "Fannie Aubut")
        self.assertEqual(applicant.letter_count, 1)

    # ------------------------------------------------------------------
    # 2. Le montant s'écrit comme il se lit
    # ------------------------------------------------------------------

    def test_the_amount_is_formatted_not_a_raw_float(self):
        applicant = self._make_applicant(salary=68000.0)
        rendu = applicant.salary_proposed_display
        self.assertTrue(rendu)
        self.assertNotIn(
            "68000.0", rendu,
            "Le flottant brut part dans la lettre : « 68000.0 » dans une offre.",
        )
        action = applicant.with_user(self.recruiter).action_draft_offer_letter()
        body = str(self.env["letter.document"].browse(action["res_id"]).body_html)
        self.assertIn(rendu, body)
        self.assertNotIn("68000.0", body)

    def test_no_salary_but_conditions_is_enough(self):
        """Tout le monde n'est pas salarié : les conditions seules suffisent."""
        applicant = self._make_applicant(salary=0.0, extra="Taux horaire à discuter, plus commissions")
        action = applicant.with_user(self.recruiter).action_draft_offer_letter()
        body = str(self.env["letter.document"].browse(action["res_id"]).body_html)
        self.assertIn("commissions", body)
        self.assertIn("à préciser", body, "Le salaire absent doit se dire, pas valoir zéro.")

    # ------------------------------------------------------------------
    # 3. Les deux refus
    # ------------------------------------------------------------------

    def test_an_offer_without_any_conditions_is_refused(self):
        applicant = self._make_applicant(salary=0.0, extra=False)
        with self.assertRaises(UserError, msg=(
            "Une offre a été produite alors qu'elle annoncerait un salaire de zéro."
        )):
            applicant.with_user(self.recruiter).action_draft_offer_letter()

    def test_an_offer_over_a_recorded_refusal_is_refused(self):
        applicant = self._make_applicant()
        motif = self.env["hr.applicant.refuse.reason"].search([], limit=1)
        applicant.write({
            "decision_note": "Habilitation absente.",
            "refuse_reason_id": motif.id,
            "refuse_date": "2026-06-15 09:00:00",
        })
        with self.assertRaises(UserError, msg=(
            "Une offre a été rédigée par-dessus un refus consigné."
        )):
            applicant.with_user(self.recruiter).action_draft_offer_letter()

    # ------------------------------------------------------------------
    # 4. 🔴 Le salaire ne sort pas par la lettre
    # ------------------------------------------------------------------

    def test_the_offer_letter_is_out_of_reach_of_a_plain_employee(self):
        """La paire, pas l'absence : l'employé est refusé, le recruteur servi."""
        applicant = self._make_applicant()
        action = applicant.with_user(self.recruiter).action_draft_offer_letter()
        letter_id = action["res_id"]
        # ⚠️ Le cache de la transaction appartient à la TRANSACTION, pas à
        # l'environnement : sans cette invalidation, la lecture faite plus haut
        # sous un compte privilégié servirait la valeur au compte restreint et
        # le contrôle passerait sans rien prouver.
        self.env.invalidate_all()

        Letter = self.env["letter.document"]
        self.assertNotIn(
            letter_id, Letter.with_user(self.plain).search([]).ids,
            "Un employé ordinaire voit l'offre dans la liste des lettres.",
        )
        with self.assertRaises(AccessError, msg=(
            "Un employé ordinaire lit l'offre, donc le salaire proposé, que le "
            "coeur réserve aux recruteurs."
        )):
            Letter.with_user(self.plain).browse(letter_id).read(["body_html"])

        self.assertIn(
            letter_id, Letter.with_user(self.recruiter).search([]).ids,
            "Le recruteur est refusé lui aussi : la règle ne discrimine pas, "
            "elle bloque tout le monde.",
        )

    def test_a_plain_letter_stays_readable_by_everyone(self):
        """Le contrôle ne doit pas avoir simplement fermé le modèle."""
        # ⚠️ `partner_id` est REQUIS sur `letter.document` : une lettre
        # s'adresse toujours à quelqu'un.
        ordinaire = self.env["letter.document"].create({
            "name": "Lettre de service", "company_id": self.company.id,
            "partner_id": self.env.ref("base.partner_admin").id,
            "body_html": "<p>Bonjour.</p>",
        })
        self.env.invalidate_all()
        self.assertIn(
            ordinaire.id, self.env["letter.document"].with_user(self.plain).search([]).ids,
            "La règle a fermé toutes les lettres, pas seulement celles de "
            "recrutement : ce n'est plus une restriction, c'est une panne.",
        )

    def test_the_formatted_salary_carries_the_core_restriction(self):
        applicant = self._make_applicant()
        self.env.invalidate_all()
        with self.assertRaises(AccessError, msg=(
            "Le champ mis en forme rend le salaire à qui n'y a pas droit."
        )):
            applicant.with_user(self.plain).read(["salary_proposed_display"])

    # ------------------------------------------------------------------
    # 5. La palette de fusion : ce qui la rend nécessaire, et sa limite
    # ------------------------------------------------------------------

    def test_a_plain_recruiter_can_render_the_shipped_template(self):
        """🔴 Sans la palette, le socle refuse le rendu à tout recruteur.

        `mail.render.mixin` n'autorise à qui n'est pas éditeur de gabarits que
        sept expressions. Aucune offre utile n'y tient.
        """
        applicant = self._make_applicant()
        self.assertFalse(
            self.recruiter.has_group("mail.group_mail_template_editor"),
            "Le contrôle ne prouverait rien avec un recruteur déjà éditeur.",
        )
        applicant.with_user(self.recruiter).action_draft_offer_letter()

    def test_a_token_outside_the_palette_is_still_refused(self):
        """La palette élargit, elle n'ouvre pas."""
        applicant = self._make_applicant()
        template = self.env.ref("bf_recruitment_letter.letter_template_job_offer")
        template.sudo().body_html = (
            "<p>{{ object.applicant_id.candidate_id.email_from }}</p>"
        )
        with self.assertRaises(AccessError, msg=(
            "Un champ hors palette est rendu : la liste blanche n'est plus une "
            "liste blanche."
        )):
            applicant.with_user(self.recruiter).action_draft_offer_letter()

    # ------------------------------------------------------------------
    # 6. Le contact
    # ------------------------------------------------------------------

    def test_an_applicant_without_a_contact_is_refused_clearly(self):
        """`letter.document.partner_id` est requis : sans garde, erreur SQL brute."""
        applicant = self._make_applicant(with_partner=False)
        self.assertFalse(
            applicant.partner_id,
            "Le montage n'a pas produit la candidature sans contact qu'il "
            "prétend éprouver.",
        )
        with self.assertRaises(UserError, msg=(
            "La contrainte SQL est remontée telle quelle à l'écran."
        )):
            applicant.with_user(self.recruiter).action_draft_offer_letter()

    def test_every_token_of_the_shipped_template_is_in_the_palette(self):
        """🔴 Le contrôle qui aurait fait gagner une heure.

        Un champ de fusion hors palette ne casse rien à l'installation, ne dit
        rien au journal, et ne se voit qu'au moment où un recruteur ordinaire
        applique le gabarit, c'est-à-dire chez le client. Ce test lit le
        gabarit TEL QU'IL EST EN BASE, pas tel qu'il est au fichier : c'est la
        seule lecture qui compte, puisque `noupdate="1"` fait que les deux
        peuvent diverger pour toujours.
        """
        template = self.env.ref("bf_recruitment_letter.letter_template_job_offer")
        autorisees = self.env["letter.document"].mail_allowed_qweb_expressions()
        for source, texte in (("corps", template.body_html), ("objet", template.subject)):
            for expression in [i[1] for i in parse_inline_template(str(texte or "")) if i[1]]:
                self.assertIn(
                    expression.strip(), autorisees,
                    "Le %s du gabarit utilise « %s », hors de la palette : le "
                    "rendu lèvera chez tout recruteur qui n'est pas éditeur de "
                    "gabarits." % (source, expression.strip()),
                )
