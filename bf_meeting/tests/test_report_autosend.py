"""L'envoi automatique du compte rendu, et surtout ce qu'il refuse d'envoyer.

Ces tests gardent quatre promesses, dans cet ordre d'importance :

* **la permission est une liste blanche portée par le projet** : un compte
  rendu qui n'est pas dans un projet marqué ne part jamais tout seul, quoi que
  demande l'appelant — c'est ce qui protège tous les autres comptes rendus ;
* **un compte rendu sans destinataire ne part pas**, même dans un projet
  marqué : `action_send_report_direct` ne se rabat pas sur les participants ;
* **les destinataires par défaut du projet s'héritent**, à la création comme
  au moment où le projet est posé après coup — c'est le cas réel d'un appel,
  dont le projet n'arrive que plusieurs minutes plus tard ;
* **rien ne part deux fois** : un compte rendu déjà envoyé ne se réexpédie pas
  au passage suivant du déclencheur.
"""

from odoo import Command
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestReportAutosend(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.destinataire = cls.env['res.partner'].create({
            'name': 'Alice Exemple', 'email': 'alice@essai.test'})
        cls.autre = cls.env['res.partner'].create({
            'name': 'Bruno Exemple', 'email': 'bruno@essai.test'})
        # Le projet dédié : marqué, avec son destinataire par défaut.
        cls.projet_ligne = cls.env['project.project'].create({
            'name': 'Appels de la ligne dédiée',
            'meeting_autosend': True,
            'meeting_report_recipient_ids': [Command.set(cls.destinataire.ids)],
        })
        # Le projet ordinaire : celui de tout le reste du parc.
        cls.projet_ordinaire = cls.env['project.project'].create({
            'name': 'Mandat ordinaire'})

    def _compte_rendu(self, **overrides):
        vals = {
            'name': 'Appel du banc',
            'date': '2026-09-17 18:00:00',
            'duration_minutes': 12,
        }
        vals.update(overrides)
        return self.env['meeting.record'].create(vals)

    def _courriels_de(self, record):
        return self.env['mail.mail'].search([
            ('model', '=', 'meeting.record'), ('res_id', '=', record.id)])

    # ── La garde ─────────────────────────────────────────────────────────

    def test_projet_non_marque_ne_part_pas(self):
        """Le cas qui compte : un compte rendu d'un projet ordinaire, avec des
        destinataires bien remplis, reste sur place."""
        cr = self._compte_rendu(
            project_id=self.projet_ordinaire.id,
            report_recipient_ids=[Command.set(self.autre.ids)],
        )
        self.assertFalse(cr.action_send_report_auto())
        self.assertEqual(cr.report_state, 'draft')
        self.assertFalse(self._courriels_de(cr))

    def test_sans_projet_ne_part_pas(self):
        """Un compte rendu que personne n'a encore rangé : pas de projet, donc
        pas de permission."""
        cr = self._compte_rendu(
            report_recipient_ids=[Command.set(self.autre.ids)])
        self.assertFalse(cr.action_send_report_auto())
        self.assertEqual(cr.report_state, 'draft')
        self.assertFalse(self._courriels_de(cr))

    def test_projet_marque_sans_destinataire_ne_part_pas(self):
        """Le drapeau ne suffit pas : sans destinataire, l'envoi est refusé au
        lieu de se rabattre sur les participants."""
        self.projet_ligne.meeting_report_recipient_ids = [Command.clear()]
        cr = self._compte_rendu(project_id=self.projet_ligne.id)
        self.assertFalse(cr.report_recipient_ids)
        self.assertFalse(cr.action_send_report_auto())
        self.assertEqual(cr.report_state, 'draft')
        self.assertFalse(self._courriels_de(cr))

    def test_deja_envoye_ne_repart_pas(self):
        cr = self._compte_rendu(project_id=self.projet_ligne.id)
        self.assertTrue(cr.action_send_report_auto())
        premiers = self._courriels_de(cr)
        self.assertTrue(premiers)
        self.assertFalse(cr.action_send_report_auto())
        self.assertEqual(self._courriels_de(cr), premiers)

    # ── Le chemin qui doit marcher ───────────────────────────────────────

    def test_projet_marque_part_au_bon_destinataire(self):
        cr = self._compte_rendu(project_id=self.projet_ligne.id)
        self.assertEqual(cr.report_recipient_ids, self.destinataire)
        self.assertTrue(cr.action_send_report_auto())
        self.assertEqual(cr.report_state, 'sent')
        self.assertTrue(cr.report_sent_date)
        self.assertFalse(cr.report_sent_manually)
        courriels = self._courriels_de(cr)
        self.assertEqual(len(courriels), 1)
        self.assertIn(
            self.destinataire, courriels.recipient_ids | courriels.partner_ids)
        # La trace au chatter : c'est par là qu'on voit, des mois plus tard,
        # qu'un humain n'a pas cliqué.
        self.assertTrue(cr.message_ids.filtered(
            lambda m: 'automatiquement' in (m.body or '')))

    # ── L'héritage des destinataires ─────────────────────────────────────

    def test_heritage_a_la_creation(self):
        cr = self._compte_rendu(project_id=self.projet_ligne.id)
        self.assertEqual(cr.report_recipient_ids, self.destinataire)

    def test_heritage_quand_le_projet_arrive_apres(self):
        """Le cas réel d'un appel : le compte rendu naît sans projet, il est
        rangé quelques minutes plus tard."""
        cr = self._compte_rendu()
        self.assertFalse(cr.report_recipient_ids)
        cr.project_id = self.projet_ligne
        self.assertEqual(cr.report_recipient_ids, self.destinataire)

    def test_heritage_n_ecrase_pas_un_choix_explicite(self):
        cr = self._compte_rendu(
            project_id=self.projet_ligne.id,
            report_recipient_ids=[Command.set(self.autre.ids)],
        )
        self.assertEqual(cr.report_recipient_ids, self.autre)
        cr.project_id = self.projet_ordinaire
        self.assertEqual(cr.report_recipient_ids, self.autre)

    def test_projet_ordinaire_n_herite_de_rien(self):
        cr = self._compte_rendu(project_id=self.projet_ordinaire.id)
        self.assertFalse(cr.report_recipient_ids)

    # ── La marque : le compte rendu suit la société de son projet ────────

    def test_societe_du_projet_a_la_creation(self):
        """Le PDF et le courriel lisent company_id : sans cet héritage, le
        compte rendu d'un client sort aux couleurs de qui l'a créé."""
        cliente = self.env['res.company'].create({'name': 'Cliente Exemple'})
        projet = self.env['project.project'].create({
            'name': 'Appels de la cliente', 'company_id': cliente.id})
        cr = self._compte_rendu(project_id=projet.id)
        self.assertEqual(cr.company_id, cliente)

    def test_societe_suit_le_projet_pose_apres(self):
        cliente = self.env['res.company'].create({'name': 'Cliente Exemple 2'})
        projet = self.env['project.project'].create({
            'name': 'Appels de la cliente 2', 'company_id': cliente.id})
        cr = self._compte_rendu()
        self.assertNotEqual(cr.company_id, cliente)
        cr.project_id = projet
        self.assertEqual(cr.company_id, cliente)

    def test_projet_sans_societe_ne_change_rien(self):
        cr = self._compte_rendu(project_id=self.projet_ordinaire.id)
        self.assertEqual(cr.company_id, self.env.company)


@tagged('post_install', '-at_install')
class TestAccentSurFondSombre(TransactionCase):
    """La sur-ligne de la bannière doit rester lisible quelle que soit la marque.

    Mesuré avant correctif : Deep Teal #135466 sur Deep Navy #2C3448 = 1,47:1,
    soit la moitié du seuil AA du grand texte. Une marque à primaire claire ne
    le voyait pas, son couple étant à 5,0:1.
    """

    @staticmethod
    def _contraste(a, b):
        from ..models.meeting_record import _contraste
        return _contraste(a, b)

    def _record(self, primaire, sombre):
        company = self.env['res.company'].create({
            'name': f'Marque {primaire}',
            'report_brand_primary': primaire,
            'report_brand_dark': sombre,
        })
        return self.env['meeting.record'].create({
            'name': 'Bannière', 'date': '2026-09-17 18:00:00',
            'company_id': company.id,
        })

    def test_primaire_foncee_est_eclaircie(self):
        cr = self._record('#135466', '#2C3448')
        accent = cr.report_accent_on_dark()
        self.assertNotEqual(accent.upper(), '#135466')
        self.assertGreaterEqual(self._contraste(accent, '#2C3448'), 3.0)

    def test_primaire_deja_lisible_reste_intacte(self):
        cr = self._record('#29ABE2', '#2E3132')
        self.assertEqual(cr.report_accent_on_dark().upper(), '#29ABE2')

    def test_couleur_invalide_ne_fait_pas_tomber_le_rapport(self):
        cr = self._record('pas-une-couleur', '#2C3448')
        self.assertEqual(cr.report_accent_on_dark(), 'pas-une-couleur')
