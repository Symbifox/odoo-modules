"""le registre des abonnements ne vient plus avec le statut d'employé.

Sous de VRAIS comptes, jamais uid 1 : le superutilisateur contourne les listes
de contrôle et chaque essai passerait sans rien prouver. Le cache de l'ORM est
vidé avant chaque lecture d'un rôle, pour qu'un chiffre lu en sudo ne lui soit
pas resservi.
"""
import importlib.util
import os

from odoo import Command, fields
from odoo.exceptions import AccessError
from odoo.modules.module import get_module_path
from odoo.tests import TransactionCase, tagged

REGISTRE = 'bf_subscription.group_subscription_user'


@tagged('post_install', '-at_install')
class TestAccesRegistre(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        Users = cls.env['res.users'].with_context(no_reset_password=True)
        interne = cls.env.ref('base.group_user')
        cls.groupe = cls.env.ref(REGISTRE)
        cls.employe = Users.create({
            'name': 'Employé essai', 'login': 'employe_essai',
            'email': 'employe@registre.invalid', 'groups_id': [Command.set([interne.id])],
        })
        # Saisit des factures fournisseurs, sans qu'on lui ait attribué le registre.
        cls.facturation = Users.create({
            'name': 'Facturation essai', 'login': 'facturation_essai',
            'email': 'facturation@registre.invalid',
            'groups_id': [Command.set([interne.id, cls.env.ref('account.group_account_invoice').id])],
        })
        cls.attribue = Users.create({
            'name': 'Registre essai', 'login': 'registre_essai',
            'email': 'registre@registre.invalid',
            'groups_id': [Command.set([interne.id, cls.groupe.id])],
        })
        # Le témoin positif de la facture : facturation ET registre.
        cls.facturation_registre = Users.create({
            'name': 'Facturation registre essai', 'login': 'facturation_registre_essai',
            'email': 'facturation.registre@registre.invalid',
            'groups_id': [Command.set([interne.id, cls.env.ref('account.group_account_invoice').id,
                                       cls.groupe.id])],
        })
        cls.vendor = cls.env['res.partner'].create({'name': 'Fournisseur essai', 'supplier_rank': 1})
        cls.sub = cls.env['subscription.subscription'].create({
            'name': 'Forfait essai', 'vendor_id': cls.vendor.id,
            'company_id': cls.env.company.id, 'cycle': 'monthly', 'cycle_amount': 100.0,
            'state': 'active', 'start_date': fields.Date.today(),
            'price_alert_threshold_pct': 5.0, 'responsible_id': cls.attribue.id,
        })

    # ── le groupe ──────────────────────────────────────────────────────────

    def test_le_statut_d_employe_n_implique_plus_le_registre(self):
        self.assertNotIn(self.groupe, self.env.ref('base.group_user').implied_ids)
        self.assertFalse(self.employe.has_group(REGISTRE))
        self.assertFalse(self.facturation.has_group(REGISTRE))

    def test_le_groupe_s_attribue_dans_les_droits_d_acces(self):
        """Une catégorie, sinon aucun écran ne le montre ni ne le retire."""
        self.assertEqual(self.groupe.category_id,
                         self.env.ref('bf_subscription.module_category_bf_subscription'))
        self.assertEqual(self.env.ref('bf_subscription.group_subscription_manager').category_id,
                         self.groupe.category_id)

    # ── le registre ────────────────────────────────────────────────────────

    def test_l_employe_ne_lit_ni_ne_cree_d_abonnement(self):
        self.env.invalidate_all()
        Sub = self.env['subscription.subscription'].with_user(self.employe)
        with self.assertRaises(AccessError):
            self.sub.with_user(self.employe).read(['cycle_amount'])
        with self.assertRaises(AccessError):
            Sub.search([])
        with self.assertRaises(AccessError):
            Sub.create({'name': 'Essai', 'vendor_id': self.vendor.id,
                        'company_id': self.env.company.id, 'cycle_amount': 1.0})

    def test_temoin_positif_qui_a_le_registre_le_lit(self):
        """Sans lui, les refus ci-dessus pourraient venir d'autre chose."""
        self.env.invalidate_all()
        self.assertEqual(self.sub.with_user(self.attribue).read(['cycle_amount'])[0]['cycle_amount'], 100.0)

    # ── la fiche contact : tout employé l'ouvre ────────────────────────────

    def test_la_fiche_contact_s_ouvre_sans_le_registre(self):
        Partner = self.env['res.partner'].with_user(self.employe)
        arch = Partner.get_views([(False, 'form')])['views']['form']['arch']
        for nom in ('managed_subscription_count', 'own_subscription_count',
                    'action_view_managed_subscriptions', 'action_view_own_subscriptions'):
            self.assertNotIn(nom, arch)
        # ⚠️ Pas un web_read de toute l'arch : d'autres modules (resource_booking)
        # y calculent des compteurs que ce compte de base.group_user seul ne lit
        # pas non plus. On mesure ce que CE module y met : absent de l'arch,
        # illisible par RPC.
        self.env.invalidate_all()
        self.assertEqual(self.vendor.with_user(self.employe).read(['name'])[0]['name'], self.vendor.name)
        with self.assertRaises(AccessError):
            self.vendor.with_user(self.employe).read(['own_subscription_count'])

    def test_temoin_positif_la_fiche_contact_porte_les_compteurs(self):
        arch = self.env['res.partner'].with_user(self.attribue).get_views([(False, 'form')])['views']['form']['arch']
        self.assertIn('own_subscription_count', arch)
        self.env.invalidate_all()
        self.assertEqual(self.vendor.with_user(self.attribue).own_subscription_count, 1)

    # ── la facture fournisseur : se saisit sans le registre ────────────────

    def _alerte_de(self, facture):
        """La note d'alerte nomme la facture par son lien ; son texte est dans la
        langue du responsable, l'essai ne s'y fie donc pas."""
        return self.sub.message_ids.filtered(
            lambda m: 'account.move' in (m.body or '') and str(facture.id) in (m.body or ''))

    def _facture(self, user, montant):
        return self.env['account.move'].with_user(user).create({
            'move_type': 'in_invoice', 'partner_id': self.vendor.id,
            'invoice_date': fields.Date.today(),
            'invoice_line_ids': [Command.create({'name': 'Forfait', 'quantity': 1, 'price_unit': montant})],
        })

    def test_la_facture_s_accroche_et_alerte_sans_le_registre(self):
        """🔴 L'accrochage automatique et l'alerte de hausse lisent le registre.
        Hors sudo, une facture saisie par qui ne l'a pas levait une erreur
        d'accès et ne se créait pas."""
        self.env.invalidate_all()
        facture = self._facture(self.facturation, 108.0)  # +8 % : au-delà des 5 %, dans les ±10 %
        self.assertEqual(facture.sudo().subscription_id, self.sub)
        self.assertTrue(self._alerte_de(facture))
        arch = self.env['account.move'].with_user(self.facturation).get_views([(False, 'form')])['views']['form']['arch']
        self.assertNotIn('action_open_rebill_wizard', arch)
        self.assertNotIn('name="subscription_id"', arch)

    def test_la_facture_deja_liee_alerte_sans_le_registre(self):
        """🔴 Le chemin que l'accrochage automatique masque : sa recherche en sudo
        met l'abonnement en cache, et l'alerte le lirait ensuite sans contrôle
        même hors sudo (mutant survivant). Une facture qui ARRIVE liée,
        cache vidé, oblige l'alerte à relire le registre."""
        self.env.invalidate_all()
        facture = self.env['account.move'].with_user(self.facturation).create({
            'move_type': 'in_invoice', 'partner_id': self.vendor.id,
            'invoice_date': fields.Date.today(), 'subscription_id': self.sub.id,
            'invoice_line_ids': [Command.create({'name': 'Forfait', 'quantity': 1, 'price_unit': 108.0})],
        })
        self.assertTrue(self._alerte_de(facture))

    def test_temoin_positif_la_facture_porte_le_lien_pour_le_registre(self):
        arch = self.env['account.move'].with_user(self.facturation_registre).get_views(
            [(False, 'form')])['views']['form']['arch']
        self.assertIn('name="subscription_id"', arch)
        self.assertIn('action_open_rebill_wizard', arch)

    # ── ce qui part vers des personnes ─────────────────────────────────────

    def test_l_alerte_de_hausse_n_assigne_rien_a_l_acheteur_sans_registre(self):
        """Sans responsable, l'activité allait à qui avait saisi la facture : un
        acheteur sans le registre recevait fournisseur et montant par courriel."""
        self.sub.responsible_id = False
        facture = self._facture(self.facturation, 108.0)
        self.assertFalse(self.env['mail.activity'].search([
            ('res_model', '=', 'subscription.subscription'), ('res_id', '=', self.sub.id),
            ('user_id', '=', self.facturation.id)]))
        # Témoin : la note du fil, elle, est bien là — l'alerte a joué.
        self.assertTrue(self._alerte_de(facture))

    def test_le_responsable_et_les_destinataires_se_choisissent_dans_le_registre(self):
        for modele, champ in (('subscription.subscription', 'responsible_id'),
                              ('subscription.digest', 'user_ids')):
            domaine = self.env[modele].fields_get([champ])[champ]['domain']
            choix = self.env['res.users'].search(domaine)
            self.assertIn(self.attribue, choix, modele)
            self.assertNotIn(self.employe, choix, modele)

    def test_le_recapitulatif_saute_le_destinataire_sans_registre(self):
        from unittest.mock import patch
        digest = self.env['subscription.digest'].create({
            'name': 'Récap essai', 'company_id': self.env.company.id,
            'user_ids': [Command.set([self.employe.id, self.attribue.id])],
        })
        envoyes = []
        Mail = type(self.env['mail.mail'])
        original = Mail.create

        def capter(mails, vals_list):
            res = original(mails, vals_list)
            envoyes.extend(res.mapped('email_to'))
            return res
        with patch.object(Mail, 'create', capter), patch.object(Mail, 'send', lambda *a, **k: True):
            digest._send_digest()
        self.assertTrue(any('registre@registre.invalid' in (e or '') for e in envoyes),
                        "témoin : le destinataire qui a le registre reçoit le récapitulatif")
        self.assertFalse(any('employe@registre.invalid' in (e or '') for e in envoyes))

    # ── la migration ───────────────────────────────────────────────────────

    def _migration(self):
        chemin = os.path.join(get_module_path('bf_subscription'),
                              'migrations', '18.0.1.10.0', 'post-migrate.py')
        spec = importlib.util.spec_from_file_location('bf_subscription_migration_essai', chemin)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.migrate

    def test_la_migration_vide_le_groupe_selon_la_regle(self):
        """L'état d'avant, rejoué : l'implication, et le groupe chez tout le monde.

        🔴 Retirer le lien ne sort personne du groupe : l'essai mesure que la
        migration le vide elle-même, et qu'elle garde les bons comptes."""
        interne = self.env.ref('base.group_user')
        interne.write({'implied_ids': [Command.link(self.groupe.id)]})
        self.assertTrue(self.employe.has_group(REGISTRE), "état d'avant mal rejoué")
        self.assertTrue(self.facturation.has_group(REGISTRE), "état d'avant mal rejoué")
        archive = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Archivé essai', 'login': 'archive_essai',
            'groups_id': [Command.set([interne.id])],
        })
        archive.active = False
        self.assertIn(archive, self.groupe.with_context(active_test=False).users,
                      "état d'avant mal rejoué : l'archivé n'avait pas le registre")

        self._migration()(self.env.cr, '18.0.1.9.0')
        self.env.invalidate_all()

        self.assertNotIn(self.groupe, interne.implied_ids)
        rel = self.groupe.with_context(active_test=False).users
        self.assertNotIn(self.employe, rel)
        self.assertNotIn(archive, rel)
        self.assertIn(self.facturation, rel, "les droits de facturation gardent le registre")
        self.assertIn(self.attribue.browse(self.env.ref('base.user_admin').id), rel,
                      "l'administrateur le garde, par le gestionnaire")
        # Le témoin attribué à la main n'a pas les droits de facturation : la
        # règle le retire. C'est voulu, et dit pour que personne ne s'en étonne.
        self.assertNotIn(self.attribue, rel)
        # Le modèle des nouveaux usagers n'est pas une personne : il perd le registre
        # même s'il porte la facturation, sinon tout usager créé ensuite le recevrait.
        modele = self.env.ref('base.default_user')
        self.assertNotIn(modele, rel)
        nouveau = self.env['res.users'].with_context(no_reset_password=True).create(
            {'name': 'Nouveau essai', 'login': 'nouveau_essai'})
        self.assertFalse(nouveau.has_group(REGISTRE), "un nouvel usager reçoit le registre par le modèle")
        self.env.invalidate_all()
        with self.assertRaises(AccessError):
            self.sub.with_user(self.employe).read(['cycle_amount'])
