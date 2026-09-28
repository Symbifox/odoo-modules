"""Le nom du projet et la société entrent échappés dans le courriel du rapport.

Un nom de projet `<a href=x>` devenait un vrai lien dans le courriel envoyé
aux destinataires externes, et le nom de société une vraie balise.
"""
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestReportEscaping(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.company.name = 'Société <b>piégée</b>'
        cls.destinataire = cls.env['res.partner'].create({
            'name': 'Destinataire', 'email': 'dest@example.invalid',
        })
        cls.projet = cls.env['project.project'].create({'name': '<a href=x>piège</a>'})
        cls.matrice = cls.env['project.knowledge.matrix'].create({
            'name': 'Matrice', 'project_id': cls.projet.id,
            'recipient_ids': [(6, 0, cls.destinataire.ids)],
        })

    def _envoyer(self):
        with patch.object(type(self.matrice), '_get_pdf_binary', lambda s: b'%PDF-1.4'), \
                patch('odoo.addons.mail.models.mail_mail.MailMail.send', lambda s, *a, **k: None):
            self.matrice._send_report_to_recipients()
        return self.env['mail.mail'].search(
            [('recipient_ids', 'in', self.destinataire.ids)], order='id desc', limit=1)

    def test_le_nom_du_projet_arrive_echappe(self):
        corps = str(self._envoyer().body_html)
        self.assertNotIn('<a href=x>', corps)
        self.assertIn('&lt;a href=x&gt;piège&lt;/a&gt;', corps)

    def test_la_societe_arrive_echappee(self):
        corps = str(self._envoyer().body_html)
        self.assertNotIn('<b>piégée</b>', corps)
        self.assertIn('Société &lt;b&gt;piégée&lt;/b&gt;', corps)

    def test_l_assistant_echappe_aussi(self):
        assistant = self.env['knowledge.matrix.send.wizard'].create({'matrix_id': self.matrice.id})
        self.assertNotIn('<a href=x>', str(assistant.body))
        enveloppe = str(assistant._wrap_branded_body('<p>test</p>'))
        self.assertIn('Société &lt;b&gt;piégée&lt;/b&gt;', enveloppe)
        self.assertIn('<p>test</p>', enveloppe)
