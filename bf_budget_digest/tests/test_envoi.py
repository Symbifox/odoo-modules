"""L'envoi, de bout en bout : du cron du cœur jusqu'au courriel sorti.

Les autres essais du pont s'arrêtent au rendu (`_render_budget_section`,
`_splice_budget_section`). Ceux-ci empruntent le chemin que la production
emprunte — `_send_digest` du cœur, qui crée le `mail.mail` et l'envoie — et
regardent ce qui franchit la passerelle SMTP (simulée par le cadre `mail`) :
le destinataire, l'objet, et la section Budget dans le corps. C'est la seule
preuve qu'un vert au rendu ne donne pas : que la section survit au vrai
assemblage du briefing, et qu'un jour calme sort un briefing SANS elle,
plutôt que pas de briefing du tout.
"""
from odoo.tests.common import tagged

from odoo.addons.bf_budget.tests.common import BfBudgetCommon
from odoo.addons.mail.tests.common import MockEmail

#: Le titre que `_render_budget_section` pose en tête de section. L'émoji sert
#: de marqueur : « Budget » nu pourrait venir d'une autre section du briefing.
MARQUEUR_SECTION = "\N{MONEY BAG} Budget"


@tagged("post_install", "-at_install")
class TestEnvoiBoutEnBout(BfBudgetCommon, MockEmail):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Le `De:` du briefing prend l'adresse de la société du destinataire :
        # sans elle, le décor retomberait sur l'adresse du superutilisateur.
        cls.company.email = "briefing@societe-budget.example.com"
        cls.config = cls.env["daily.digest.config"].create({
            "name": "Digest d'essai envoi", "company_id": cls.company.id,
        })
        groupe = cls.env.ref("bf_budget.group_bf_budget_user")
        cls.responsable = cls.env["res.users"].create({
            "name": "Responsable du budget",
            "login": "resp_budget_envoi",
            "email": "resp.budget@societe-budget.example.com",
            "lang": "en_US",
            "company_id": cls.company.id,
            "company_ids": [(6, 0, [cls.company.id])],
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id, groupe.id])],
        })

    def _courriels_pour(self, adresse):
        """Les courriels capturés par la passerelle pour cette adresse."""
        return [
            mail for mail in self._mails
            if adresse in (mail.get("email_to") or [])
        ]

    def test_la_ligne_en_alerte_part_dans_le_briefing(self):
        budget = self._make_budget(planned=1200.0, state="open")
        budget.user_id = self.responsable
        self._post_bill(self.account_software, 12000.0)
        self.env.invalidate_all()
        self.assertTrue(budget.line_ids.is_alert, "le décor doit vraiment alerter")

        with self.mock_mail_gateway():
            self.config._send_digest(test_user=self.responsable)

        sortis = self._courriels_pour(self.responsable.email)
        self.assertEqual(len(sortis), 1, "un briefing, exactement un")
        courriel = sortis[0]
        self.assertIn("Your day", courriel["subject"])
        corps = courriel["body"]
        self.assertIn(MARQUEUR_SECTION, corps)
        self.assertIn(self.position_software.name, corps)
        self.assertIn(budget.display_name, corps)
        # L'écart du courriel porte le nom que l'écran emploie, pas un autre.
        self.assertIn("Variance from theoretical", corps)

    def test_un_jour_calme_envoie_le_briefing_sans_la_section(self):
        """Le briefing du matin part quand même : c'est la section qui se
        tait, jamais le courriel entier."""
        budget = self._make_budget(planned=1200.0, state="open")
        budget.user_id = self.responsable

        with self.mock_mail_gateway():
            self.config._send_digest(test_user=self.responsable)

        sortis = self._courriels_pour(self.responsable.email)
        self.assertEqual(len(sortis), 1, "le briefing part, section ou pas")
        self.assertNotIn(MARQUEUR_SECTION, sortis[0]["body"])
