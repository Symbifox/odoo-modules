"""Le lot 10 : corbeille, codes de relève, inventaire, et ce qu'ils ne doivent
pas casser.

Trois de ces fonctions touchent au chemin d'accès d'un coffre qui porte des
graines vivantes. Les tests d'ici ne cherchent donc pas seulement à prouver que
ça marche : ils cherchent surtout à prouver que les anciennes garanties tiennent
toujours, à savoir qu'aucune graine n'est lisible, qu'un coffre n'appartient
qu'à une personne, et que rien ne détruit sans qu'on l'ait demandé deux fois.
"""

import ast
import os

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from .test_otp_vault import OtpCase, _chiffre_credible


@tagged('post_install', '-at_install')
class TestLaCorbeille(OtpCase):
    """Retirer est réversible, détruire est un second geste.

    🔴 Jusqu'à la 18.0.10.0.0, `delete_token` appelait `unlink()` : un clic de
    travers effaçait pour de bon un deuxième facteur dont personne n'avait la
    graine ailleurs. L'avertissement était juste, il arrivait trop tard.
    """

    def setUp(self):
        super().setUp()
        self._coffre(self.alice)
        self.tid = self._jeton(self.alice)
        self.modele = self.env['bf.otp.token'].with_user(self.alice)

    def _brut(self, tid):
        return self.env['bf.otp.token'].with_context(active_test=False).browse(tid)

    def test_retirer_range_a_la_corbeille_et_ne_detruit_rien(self):
        chiffre = self._brut(self.tid).secret_cipher
        self.modele.delete_token(self.tid)
        ligne = self._brut(self.tid)
        self.assertTrue(ligne.exists(), "le token a été détruit alors qu'on l'a seulement retiré")
        self.assertFalse(ligne.active)
        self.assertTrue(ligne.deleted_at, "on ne sait pas depuis quand il est là")
        self.assertEqual(ligne.secret_cipher, chiffre,
                         "le chiffré a bougé en passant à la corbeille")

    def test_un_token_a_la_corbeille_sort_de_la_liste_du_coffre(self):
        self.modele.delete_token(self.tid)
        self.assertEqual(self.modele.load_my_tokens(), [])
        corbeille = self.modele.load_my_trash()
        self.assertEqual(len(corbeille), 1)
        self.assertEqual(corbeille[0]['id'], self.tid)

    def test_restaurer_le_rend_intact(self):
        avant = self._brut(self.tid).secret_cipher
        self.modele.delete_token(self.tid)
        self.modele.restore_token(self.tid)
        ligne = self._brut(self.tid)
        self.assertTrue(ligne.active)
        self.assertFalse(ligne.deleted_at)
        self.assertEqual(ligne.secret_cipher, avant)
        self.assertEqual(len(self.modele.load_my_tokens()), 1)

    def test_detruire_refuse_un_token_qui_n_est_pas_a_la_corbeille(self):
        """La destruction est TOUJOURS le second geste."""
        with self.assertRaises(ValidationError):
            self.modele.purge_token(self.tid)
        self.assertTrue(self._brut(self.tid).exists())

    def test_detruire_apres_la_corbeille_detruit_pour_de_bon(self):
        self.modele.delete_token(self.tid)
        self.modele.purge_token(self.tid)
        self.assertFalse(self._brut(self.tid).exists())

    def test_vider_la_corbeille_ne_touche_pas_au_coffre(self):
        garde = self._jeton(self.alice, name='garde@exemple.com')
        self.modele.delete_token(self.tid)
        detruits = self.modele.empty_trash()
        self.assertEqual(detruits, 1)
        self.assertFalse(self._brut(self.tid).exists())
        self.assertTrue(self._brut(garde).exists())

    def test_reimporter_un_token_a_la_corbeille_ne_fait_pas_de_jumeau(self):
        """🔴 Le dédoublonnage doit VOIR la corbeille.

        Sans `active_test=False`, un token retiré ne compte pas comme connu :
        l'import en recrée un jumeau, et la restauration rend ensuite deux
        lignes identiques. Le doublon n'apparaîtrait qu'au moment où quelqu'un
        vide sa corbeille, des semaines plus tard.
        """
        self.modele.delete_token(self.tid)
        res = self.modele.import_tokens([{
            'name': 'compte@exemple.com', 'issuer': 'Exemple',
            'secret_cipher': _chiffre_credible(), 'secret_iv': 'aXYxMjM0NTY3ODkw',
        }])
        self.assertEqual(res['created'], 0)
        self.assertEqual(res['skipped'], 1)
        self.modele.restore_token(self.tid)
        self.assertEqual(len(self.modele.load_my_tokens()), 1)

    def test_un_token_a_la_corbeille_n_est_plus_utilisable(self):
        """Il est rangé, pas à moitié supprimé : il ne doit plus servir."""
        self.modele.delete_token(self.tid)
        for methode, args in (('touch_token', ()), ('toggle_favorite', ()),
                              ('bump_counter', (3,))):
            with self.subTest(methode=methode):
                with self.assertRaises(ValidationError):
                    getattr(self.modele, methode)(self.tid, *args)

    def test_bob_ne_restaure_ni_ne_detruit_le_token_d_alice(self):
        self.modele.delete_token(self.tid)
        chez_bob = self.env['bf.otp.token'].with_user(self.bob)
        for methode in ('restore_token', 'purge_token'):
            with self.subTest(methode=methode):
                with self.assertRaises(ValidationError):
                    getattr(chez_bob, methode)(self.tid)
        self.assertTrue(self._brut(self.tid).exists())

    def test_vider_la_corbeille_de_bob_ne_vide_pas_celle_d_alice(self):
        self.modele.delete_token(self.tid)
        self._coffre(self.bob)
        self.assertEqual(self.env['bf.otp.token'].with_user(self.bob).empty_trash(), 0)
        self.assertTrue(self._brut(self.tid).exists())


@tagged('post_install', '-at_install')
class TestLesCodesDeReleve(OtpCase):
    """Une porte de plus, et rien qui permette de la retrouver."""

    SCELLE = {
        'salt': 'c2VsUmVsZXZlMTIzNDU2',
        'iterations': 600000,
        'wrapped_secret': 'vhpN7+X+ut2MHTsYL6BUA7A5F5Rf/aa=',
        'wrapped_iv': 'aXYxMjM0NTY3ODkw',
    }

    def setUp(self):
        super().setUp()
        self._coffre(self.alice)
        self.coffres = self.env['bf.otp.vault'].with_user(self.alice)

    def _ajouter(self, nom='coffre-fort'):
        return self.coffres.add_recovery(
            nom, self.SCELLE['salt'], self.SCELLE['iterations'],
            self.SCELLE['wrapped_secret'], self.SCELLE['wrapped_iv'])

    def test_le_code_lui_meme_n_a_aucune_colonne(self):
        """🔴 La propriété centrale : rien ici ne permet de vérifier un code.

        Un condensat du code donnerait à qui lit la base un point de départ
        hors ligne, exactement ce que ce module refuse de fournir depuis le
        premier jour. Un mauvais code ne déchiffre simplement rien.
        """
        champs = set(self.env['bf.otp.recovery']._fields)
        for interdit in ('code', 'code_hash', 'verifier', 'hash', 'password'):
            self.assertNotIn(interdit, champs)

    def test_le_coffre_rend_ses_releves_au_navigateur(self):
        rid = self._ajouter()
        paquet = self.coffres.get_my_vault()
        self.assertIn('recoveries', paquet)
        self.assertEqual(len(paquet['recoveries']), 1)
        ligne = paquet['recoveries'][0]
        self.assertEqual(ligne['id'], rid)
        self.assertEqual(ligne['salt'], self.SCELLE['salt'])
        self.assertEqual(ligne['wrapped_secret'], self.SCELLE['wrapped_secret'])

    def test_le_garde_refuse_une_graine_dans_le_scelle(self):
        for valeur in ('JBSWY3DPEHPK3PXP', 'otpauth://totp/x?secret=JBSWY3DPEHPK3PXP'):
            with self.subTest(valeur=valeur[:20]):
                with self.assertRaises(ValidationError):
                    self.coffres.add_recovery('x', self.SCELLE['salt'], 600000,
                                              valeur, self.SCELLE['wrapped_iv'])

    def test_cinq_codes_est_le_plafond(self):
        for i in range(5):
            self._ajouter(f'enveloppe {i}')
        with self.assertRaises(UserError):
            self._ajouter('la sixième')
        self.assertEqual(len(self.coffres.get_my_vault()['recoveries']), 5)

    def test_bob_ne_voit_ni_ne_revoque_le_code_d_alice(self):
        rid = self._ajouter()
        self._coffre(self.bob)
        chez_bob = self.env['bf.otp.vault'].with_user(self.bob)
        self.assertEqual(chez_bob.get_my_vault()['recoveries'], [])
        with self.assertRaises(UserError):
            chez_bob.remove_recovery(rid)
        self.assertTrue(self.env['bf.otp.recovery'].browse(rid).exists())

    def test_revoquer_un_code_ne_touche_pas_aux_tokens(self):
        tid = self._jeton(self.alice)
        chiffre = self.env['bf.otp.token'].browse(tid).secret_cipher
        rid = self._ajouter()
        self.coffres.remove_recovery(rid)
        self.assertFalse(self.env['bf.otp.recovery'].browse(rid).exists())
        self.assertEqual(self.env['bf.otp.token'].browse(tid).secret_cipher, chiffre)

    def test_l_usage_d_un_code_s_horodate(self):
        """Un code qui sert alors que personne ne s'en souvient est le seul
        signal qu'on aura."""
        rid = self._ajouter()
        self.assertFalse(self.env['bf.otp.recovery'].browse(rid).last_used)
        self.coffres.touch_recovery(rid)
        self.assertTrue(self.env['bf.otp.recovery'].browse(rid).last_used)

    def test_supprimer_le_coffre_emporte_ses_codes(self):
        rid = self._ajouter()
        vault = self.env['bf.otp.vault'].search([('user_id', '=', self.alice.id)])
        vault.with_user(self.alice).unlink()
        self.assertFalse(self.env['bf.otp.recovery'].browse(rid).exists())

    def test_un_code_sans_coffre_est_refuse(self):
        chez_bob = self.env['bf.otp.vault'].with_user(self.bob)
        with self.assertRaises(UserError):
            chez_bob.add_recovery('x', self.SCELLE['salt'], 600000,
                                  self.SCELLE['wrapped_secret'], self.SCELLE['wrapped_iv'])


@tagged('post_install', '-at_install')
class TestLInventaire(OtpCase):
    """Le rattachement, vu depuis le client et depuis le projet."""

    def setUp(self):
        super().setUp()
        self._coffre(self.alice)
        self._coffre(self.bob)
        self.client = self.env['res.partner'].create({'name': 'Client Bidon inc.'})
        self.autre = self.env['res.partner'].create({'name': 'Client Sans Token'})
        self.projet = self.env['project.project'].create({'name': 'Mandat 2026'})

    def test_le_compteur_du_client_ne_compte_que_mes_tokens(self):
        """⚠️ Jamais en sudo : un compteur qui contournerait la règle dirait à
        un gestionnaire combien de tokens quelqu'un d'autre détient pour ce
        client. C'est déjà un renseignement."""
        self._jeton(self.alice, partner_id=self.client.id)
        self._jeton(self.alice, name='deux@exemple.com', partner_id=self.client.id)
        self._jeton(self.bob, name='bob@exemple.com', partner_id=self.client.id)
        chez_alice = self.client.with_user(self.alice)
        chez_alice.invalidate_recordset(['bf_otp_token_count'])
        self.assertEqual(chez_alice.bf_otp_token_count, 2)
        chez_bob = self.client.with_user(self.bob)
        chez_bob.invalidate_recordset(['bf_otp_token_count'])
        self.assertEqual(chez_bob.bf_otp_token_count, 1)

    def test_un_client_sans_token_compte_zero(self):
        self.assertEqual(self.autre.with_user(self.alice).bf_otp_token_count, 0)

    def test_le_compteur_du_projet(self):
        self._jeton(self.alice, project_id=self.projet.id)
        chez_alice = self.projet.with_user(self.alice)
        chez_alice.invalidate_recordset(['bf_otp_token_count'])
        self.assertEqual(chez_alice.bf_otp_token_count, 1)

    def test_un_token_a_la_corbeille_ne_compte_plus_pour_le_client(self):
        tid = self._jeton(self.alice, partner_id=self.client.id)
        self.env['bf.otp.token'].with_user(self.alice).delete_token(tid)
        chez_alice = self.client.with_user(self.alice)
        chez_alice.invalidate_recordset(['bf_otp_token_count'])
        self.assertEqual(chez_alice.bf_otp_token_count, 0)

    def test_le_compteur_reste_muet_pour_qui_n_a_pas_le_groupe(self):
        etranger = self.env['res.users'].create({
            'name': 'Sans coffre', 'login': 'sanscoffre.otp@test.invalid',
            'groups_id': [(6, 0, [self.env.ref('base.group_user').id])],
        })
        self._jeton(self.alice, partner_id=self.client.id)
        self.assertEqual(self.client.with_user(etranger).bf_otp_token_count, 0)

    def test_le_bouton_ouvre_l_inventaire_borne_a_ce_client(self):
        action = self.client.with_user(self.alice).action_bf_otp_tokens()
        self.assertEqual(action['res_model'], 'bf.otp.token')
        self.assertIn(('partner_id', '=', self.client.id), action['domain'])

    def test_aucune_vue_du_module_n_affiche_le_chiffre(self):
        """🔴 Le chiffré affiché dans une liste finit dans une capture d'écran,
        un export tableur ou un rapport. Il n'a aucune raison d'être lisible :
        personne ne peut l'ouvrir depuis le back-office de toute façon."""
        vues = self.env['ir.ui.view'].search([('model', '=', 'bf.otp.token')])
        self.assertTrue(vues, "aucune vue trouvée : le test ne prouverait rien")
        for vue in vues:
            with self.subTest(vue=vue.name):
                self.assertNotIn('secret_cipher', vue.arch)
                self.assertNotIn('secret_iv', vue.arch)

    def test_les_vues_se_chargent_sous_un_vrai_compte(self):
        """Chargées sous Alice, pas sous la racine : c'est là que les droits
        d'accès et les règles se prononcent vraiment."""
        vues = self.env['bf.otp.token'].with_user(self.alice).get_views(
            [(False, 'list'), (False, 'form'), (False, 'search')])
        self.assertEqual(set(vues['views']), {'list', 'form', 'search'})

    def test_l_import_porte_le_rattachement_et_le_favori(self):
        res = self.env['bf.otp.token'].with_user(self.alice).import_tokens([{
            'name': 'venu-d-un-export@exemple.com', 'issuer': 'Exemple',
            'secret_cipher': _chiffre_credible(), 'secret_iv': 'aXYxMjM0NTY3ODkw',
            'partner_id': self.client.id, 'project_id': self.projet.id,
            'favorite': True, 'group_name': 'Interne',
        }])
        self.assertEqual(res['created'], 1)
        token = self.env['bf.otp.token'].search([
            ('user_id', '=', self.alice.id),
            ('name', '=', 'venu-d-un-export@exemple.com')])
        self.assertEqual(token.partner_id, self.client)
        self.assertEqual(token.project_id, self.projet)
        self.assertTrue(token.favorite)
        self.assertEqual(token.group_name, 'Interne')


@tagged('post_install', '-at_install')
class TestLeModuleSeTientEnsemble(OtpCase):
    """Les contrôles de structure : ce qui se casse en silence."""

    def _racine(self):
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def test_tout_fichier_js_est_dans_le_paquet_d_actifs(self):
        """⚠️ Un fichier écrit mais absent du manifeste ne lève AUCUNE erreur :
        Odoo sert simplement un paquet sans lui, et la fonction manque à
        l'écran sans que rien ne l'explique. C'est arrivé sur ce module.
        """
        racine = self._racine()
        manifeste = ast.literal_eval(
            open(os.path.join(racine, '__manifest__.py'), encoding='utf-8').read())
        paquet = set(manifeste['assets']['web.assets_backend'])
        dossier = os.path.join(racine, 'static', 'src', 'js')
        for f in sorted(os.listdir(dossier)):
            if f.endswith('.js'):
                with self.subTest(fichier=f):
                    self.assertIn(f'bf_otp/static/src/js/{f}', paquet)

    def test_l_ordre_du_paquet_respecte_les_dependances(self):
        """`otp_app.js` importe les autres : il doit venir APRÈS eux dans le
        paquet, sinon le module se charge avant ce dont il dépend."""
        racine = self._racine()
        manifeste = ast.literal_eval(
            open(os.path.join(racine, '__manifest__.py'), encoding='utf-8').read())
        paquet = manifeste['assets']['web.assets_backend']
        js = [c for c in paquet if c.endswith('.js')]
        self.assertEqual(js[-1], 'bf_otp/static/src/js/otp_app.js')

    def test_aucun_import_dynamique_d_un_module_odoo(self):
        """🔴 Trouvé au navigateur, jamais par un test : `await import("@web/…")`
        n'est PAS résoluble.

        Le système de modules d'Odoo n'est pas de l'ESM natif. Un `import()`
        dynamique d'un spécificateur `@web/…` part au navigateur, qui lève
        « Failed to resolve module specifier ». La fonction ne s'ouvre jamais et
        rien ne l'annonce à l'écran : ni erreur visible, ni bouton inerte, juste
        un clic sans effet. La suppression d'un token a vécu comme ça depuis
        qu'elle existe, sur un coffre de cent quarante-quatre tokens en
        production. Ce test refuse la forme, parce que le symptôme, lui, ne se
        voit pas.
        """
        racine = self._racine()
        dossier = os.path.join(racine, 'static', 'src', 'js')
        fautes = []
        for f in sorted(os.listdir(dossier)):
            if not f.endswith('.js'):
                continue
            texte = open(os.path.join(dossier, f), encoding='utf-8').read()
            for ligne in texte.split('\n'):
                depouillee = ligne.strip()
                if depouillee.startswith('//') or depouillee.startswith('*'):
                    continue
                if 'import(' in depouillee and '@web/' in depouillee:
                    fautes.append(f'{f} : {depouillee[:70]}')
        self.assertFalse(
            fautes,
            "import() dynamique d'un module Odoo, qui échouera dans la page : %s"
            % fautes)

    def test_aucune_interface_ne_tutoie(self):
        """Règle de marque, et elle vaut pour ce qui sera VU : gabarits et
        chaînes traduites."""
        racine = self._racine()
        motifs = ('Choisis ', 'colle le ', 'Vide-le', 'ton coffre', 'tes tokens')
        fautes = []
        for sous in ('static/src/xml', 'static/src/js'):
            dossier = os.path.join(racine, sous)
            for f in sorted(os.listdir(dossier)):
                chemin = os.path.join(dossier, f)
                if not os.path.isfile(chemin):
                    continue
                texte = open(chemin, encoding='utf-8').read()
                for motif in motifs:
                    if motif in texte:
                        fautes.append(f'{f} : « {motif} »')
        self.assertFalse(fautes, 'tutoiement dans une interface : %s' % fautes)

    def test_aucun_tiret_cadratin_dans_ce_qui_sera_vu(self):
        """Le tiret cadratin est banni partout, y compris dans les chaînes
        d'interface. Il en restait un dans le nom de partie de confiance affiché
        par l'invite de clé d'accès."""
        racine = self._racine()
        fautes = []
        for f in sorted(os.listdir(os.path.join(racine, 'static/src/xml'))):
            texte = open(os.path.join(racine, 'static/src/xml', f), encoding='utf-8').read()
            if '—' in texte:
                fautes.append(f)
        self.assertFalse(fautes, 'tiret cadratin dans un gabarit : %s' % fautes)
