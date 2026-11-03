from datetime import timedelta

from odoo import fields
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.tests import tagged


@tagged("post_install", "-at_install")
class TestFollowupDigestSection(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.today = fields.Date.context_today(cls.env["account.move"])
        cls.lecteur = cls.env["res.users"].create({
            "name": "Lecteur du digest",
            "login": "digest.recevables@example.test",
            "email": "digest.recevables@example.test",
            "company_id": cls.env.company.id,
            "company_ids": [(6, 0, [cls.env.company.id])],
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id,
                                  cls.env.ref("account.group_account_invoice").id])],
            "bf_followup_digest": True,
        })
        cls.digest = cls.env["daily.digest.config"].create({
            "name": "Digest d'essai",
            "user_ids": [(6, 0, [cls.lecteur.id])],
            "include_weather": False,
            "include_quote": False,
            "company_id": cls.env.company.id,
        })

    def _facture(self, jours_de_retard=20):
        facture = self.init_invoice(
            "out_invoice", partner=self.partner_a, amounts=[1000.0],
            invoice_date=self.today - timedelta(days=jours_de_retard + 10), post=True)
        facture.invoice_date_due = self.today - timedelta(days=jours_de_retard)
        return facture

    def _section(self):
        return self.digest._render_overdue_invoices(self.lecteur)

    # -- la section ---------------------------------------------------------

    def test_la_section_nomme_la_facture_en_retard(self):
        facture = self._facture(jours_de_retard=20)
        section = self._section()
        self.assertIn(facture.name, section)
        self.assertIn(self.partner_a.name, section)

    def test_rien_sans_facture_en_retard(self):
        """Discrimination : la même méthode doit rendre vide quand il n'y a rien."""
        self.assertEqual(self._section().strip(), "")

    def test_le_plancher_de_retard_ecarte_une_facture_trop_fraiche(self):
        facture = self._facture(jours_de_retard=3)
        self.assertIn(facture.name, self._section())
        self.digest.overdue_invoice_min_days = 10
        self.assertEqual(self._section().strip(), "",
                         "une facture échue depuis 3 jours est sous le plancher de 10")

    def test_le_commutateur_coupe_la_section(self):
        self._facture(jours_de_retard=20)
        self.assertTrue(self._section().strip())
        self.digest.include_overdue_invoices = False
        self.assertEqual(self._section().strip(), "")

    def test_un_lecteur_non_abonne_ne_voit_rien(self):
        self._facture(jours_de_retard=20)
        self.lecteur.bf_followup_digest = False
        self.assertEqual(self._section().strip(), "",
                         "le pont ne doit pas élargir qui voit l'information financière")

    def test_la_section_ne_parait_pas_hors_facturation(self):
        """La case d'abonnement est auto-inscriptible : le groupe est exigé ici aussi."""
        self._facture(jours_de_retard=20)
        self.assertTrue(self._section().strip(), "le lecteur de la facturation la voit")
        self.lecteur.groups_id = [(3, self.env.ref("account.group_account_invoice").id)]
        self.assertEqual(self._section().strip(), "",
                         "un destinataire hors facturation ne doit pas voir les recevables")

    # -- le cron du courriel séparé ----------------------------------------

    def test_le_cron_separe_est_eteint_par_la_pose(self):
        cron = self.env.ref("bf_invoice_followup.cron_weekly_followup_digest")
        self.assertFalse(cron.active,
                         "le post_init_hook du pont doit avoir éteint le courriel séparé")

    # -- l'enveloppe --------------------------------------------------------

    def test_la_section_entre_dans_la_table_de_l_enveloppe(self):
        facture = self._facture(jours_de_retard=20)
        html = self.digest._generate_html({}, self.lecteur)
        self.assertIn(facture.name, html)
        avant = html.split(facture.name)[0]
        self.assertIn("<tr>", avant.rsplit("<table", 1)[-1] if "<table" in avant else avant,
                      "la section doit porter sa propre ligne de table")
