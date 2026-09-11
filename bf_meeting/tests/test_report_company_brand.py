"""Les couleurs du PDF viennent de la société DU DOCUMENT.

Sur un Odoo multi-société, le compte rendu d'une société secondaire sortait aux
couleurs de la société principale : les gabarits lisaient `env.company` — la
société ACTIVE de qui déclenche l'impression — alors que le logo et le pied de
page, eux, lisaient déjà `doc.company_id`. Résultat : le logo d'une marque, le
bandeau d'une autre.

Le piège est silencieux : rendu depuis l'interface avec la bonne société en
tête, le rapport sort juste. Il ne dévie que lorsque la société principale
passe devant dans `allowed_company_ids` — ce qui est le cas dès qu'on coche les
deux sociétés, et dans toute impression déclenchée par un cron ou un courriel.
C'est pourquoi ces tests forcent explicitement le MAUVAIS contexte.
"""

from odoo import Command
from odoo.tests import TransactionCase, tagged


PNG_1PX = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM"
    "IQAAAABJRU5ErkJggg=="
)


@tagged('post_install', '-at_install')
class TestReportCompanyBrand(TransactionCase):

    MAISON_PRIMARY = '#123456'
    MAISON_DARK = '#0a0a0a'
    AUTRE_PRIMARY = '#e17a4b'
    AUTRE_DARK = '#232323'

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.maison = cls.env.company
        cls.maison.write({
            'report_brand_primary': cls.MAISON_PRIMARY,
            'report_brand_dark': cls.MAISON_DARK,
        })
        cls.autre = cls.env['res.company'].create({
            'name': 'Société Seconde',
            'report_brand_primary': cls.AUTRE_PRIMARY,
            'report_brand_dark': cls.AUTRE_DARK,
            # Le bandeau appelle `image_data_uri()` sans garde : sans logo, le
            # rendu casse avant d'arriver aux couleurs.
            'meeting_logo': PNG_1PX,
        })
        cls.env.user.company_ids = [Command.link(cls.autre.id)]
        # Le gabarit rend dans `doc.lang`, et le banc n'a pas forcément fr_CA
        # installé. La couleur ne dépend pas de la langue : on prend celle de
        # l'environnement, qui est installée par construction.
        cls.langue = cls.env.user.lang or 'en_US'
        cls.projet = cls.env['project.project'].create({
            'name': 'Projet Seconde', 'company_id': cls.autre.id})
        cls.record = cls.env['meeting.record'].create({
            'name': 'Rencontre de la seconde société',
            'room_name': 'Statutaire',
            'date': '2026-09-11 17:00:00',
            'duration_minutes': 30,
            'project_id': cls.projet.id,
            'company_id': cls.autre.id,
            'summary': 'Séance de travail.',
            'lang': cls.langue,
        })
        cls.agenda = cls.env['meeting.agenda'].create({
            'name': "Ordre du jour de la seconde société",
            'date': '2026-09-12 17:00:00',
            'project_id': cls.projet.id,
            'company_id': cls.autre.id,
            'lang': cls.langue,
        })

    def _render(self, report_xmlid, docid):
        """Rendre avec la société PRINCIPALE en tête — le cas qui déviait."""
        html = self.env['ir.actions.report'].with_context(
            allowed_company_ids=[self.maison.id, self.autre.id],
        )._render_qweb_html(report_xmlid, [docid])[0]
        return html.decode() if isinstance(html, bytes) else html

    def test_compte_rendu_prend_les_couleurs_de_sa_societe(self):
        html = self._render('bf_meeting.action_report_meeting_record',
                            self.record.id)
        self.assertIn(self.AUTRE_PRIMARY, html)
        self.assertIn(self.AUTRE_DARK, html)
        self.assertNotIn(self.MAISON_PRIMARY, html)
        self.assertNotIn(self.MAISON_DARK, html)

    def test_ordre_du_jour_prend_les_couleurs_de_sa_societe(self):
        html = self._render('bf_meeting.action_report_meeting_agenda',
                            self.agenda.id)
        self.assertIn(self.AUTRE_PRIMARY, html)
        self.assertIn(self.AUTRE_DARK, html)
        self.assertNotIn(self.MAISON_PRIMARY, html)
        self.assertNotIn(self.MAISON_DARK, html)

    def test_aucun_bleu_blue_fox_en_dur_dans_les_gabarits(self):
        """Le bleu de la marque maison était écrit en clair dans l'OdJ.

        Trois règles CSS le portaient — filet d'accent, sur-titre, soulignement
        des sections — et aucune couleur de société ne pouvait les déloger.
        """
        for xmlid, docid in (
            ('bf_meeting.action_report_meeting_record', self.record.id),
            ('bf_meeting.action_report_meeting_agenda', self.agenda.id),
        ):
            with self.subTest(rapport=xmlid):
                self.assertNotIn('#29ABE1', self._render(xmlid, docid))

    def test_envoi_direct_met_la_societe_visee_en_tete(self):
        """`env.company` est le PREMIER d'`allowed_company_ids`, pas un membre.

        Le code construisait la liste à partir d'un `set()` : l'id 1 repassait
        devant et défaisait le `with_company()` sans rien signaler.
        """
        captures = []

        def espion(self_tmpl, res_id, **kwargs):
            captures.append(self_tmpl.env.context.get('allowed_company_ids'))
            return True

        destinataire = self.env['res.partner'].create({
            'name': 'Destinataire Essai', 'email': 'destinataire@essai.test'})
        self.record.write({
            'report_recipient_ids': [Command.link(destinataire.id)]})
        self.assertTrue(self.record.report_recipient_ids)
        self.patch(type(self.env['mail.template']), 'send_mail', espion)
        self.record.with_context(
            allowed_company_ids=[self.maison.id, self.autre.id],
        ).action_send_report_direct()

        self.assertTrue(captures, "send_mail n'a pas été appelé")
        self.assertEqual(captures[0][0], self.autre.id)
