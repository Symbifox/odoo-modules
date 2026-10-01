"""La langue d'envoi se rabat sur une langue ACTIVE de la base.

Le repli était `fr_CA`, codé en dur dans les deux gabarits de
courriel, les deux rapports et `_compute_lang`. Sur une base où fr_CA n'est pas
active (toute installation hors Québec, par exemple), l'envoi levait
« Invalid language code: fr_CA », et une création sans langue de contexte
(RPC) posait une valeur hors de la sélection.

Le repli devient : la langue du client, sinon celle de la personne qui
travaille, sinon celle de la société, sinon la première langue active.

Ces essais n'ont de sens que sur une base où fr_CA est inactive : sur une base
qui l'a, l'ancien repli tombait juste et ils passeraient sur un code cassé.
"""

from unittest import SkipTest

from odoo import Command
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestLangueDeRepli(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        actives = [code for code, _nom in cls.env['res.lang'].get_installed()]
        if 'fr_CA' in actives:
            raise SkipTest("fr_CA est active : l'ancien repli ne se voit pas ici")
        cls.actives = actives
        # Aucune langue nulle part : le client, et la personne qui envoie
        # quand l'appel arrive sans contexte (RPC).
        cls.client = cls.env['res.partner'].create({
            'name': 'Client sans langue', 'email': 'client@essai.test', 'lang': False})
        cls.destinataire = cls.env['res.partner'].create({
            'name': 'Destinataire', 'email': 'dest@essai.test', 'lang': False})
        cls.projet = cls.env['project.project'].create({
            'name': 'Projet sans langue', 'partner_id': cls.client.id})
        cls.sans_langue = cls.env(context=dict(cls.env.context, lang=None))

    def _courriels(self, record):
        return self.env['mail.mail'].search([
            ('model', '=', record._name), ('res_id', '=', record.id)])

    def test_creation_sans_contexte_pose_une_langue_active(self):
        cr = self.sans_langue['meeting.record'].create({
            'name': 'Appel sans contexte', 'date': '2026-09-28 14:00:00',
            'duration_minutes': 10, 'project_id': self.projet.id})
        odj = self.sans_langue['meeting.agenda'].create({
            'name': 'OdJ sans contexte', 'date': '2026-09-29 14:00:00',
            'project_id': self.projet.id})
        self.assertIn(cr.lang, self.actives)
        self.assertIn(odj.lang, self.actives)

    def test_envoi_du_compte_rendu_sans_fr_ca(self):
        cr = self.env['meeting.record'].create({
            'name': 'Appel du banc', 'date': '2026-09-28 14:00:00',
            'duration_minutes': 10, 'project_id': self.projet.id,
            'report_recipient_ids': [Command.set(self.destinataire.ids)]})
        # Une fiche d'avant, sans langue : le gabarit passe par son repli.
        cr.lang = False
        cr.action_send_report_direct()
        self.assertEqual(cr.report_state, 'sent')
        self.assertTrue(self._courriels(cr))

    def test_envoi_de_l_ordre_du_jour_sans_fr_ca(self):
        odj = self.env['meeting.agenda'].create({
            'name': 'OdJ du banc', 'date': '2026-09-29 14:00:00',
            'project_id': self.projet.id,
            'recipient_ids': [Command.set(self.destinataire.ids)]})
        odj.lang = False
        gabarit = self.env.ref('bf_meeting.meeting_agenda_mail_template')
        self.assertIn(gabarit._render_lang(odj.ids)[odj.id], self.actives)
        # Envoi immédiat : le courriel part et s'efface, la preuve est la date.
        odj.action_send_agenda()
        self.assertTrue(odj.email_sent_date)

    def test_repli_jusqu_a_la_premiere_langue_active(self):
        """Ni client, ni personne, ni société n'ont de langue : la première
        langue active, jamais une langue inventée."""
        self.env.company.partner_id.lang = False
        cr = self.sans_langue['meeting.record'].create({
            'name': 'Appel nu', 'date': '2026-09-28 14:00:00',
            'duration_minutes': 10})
        self.assertEqual(cr.lang, self.actives[0])
