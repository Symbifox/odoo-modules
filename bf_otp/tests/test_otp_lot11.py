"""Le lot 11 : l'archive, le regroupement proposé, et l'export en clair.

Chacun de ces quatre gains touchait une décision déjà prise, et c'est ce que
ces essais figent : pas seulement que le code marche, mais que les arbitrages
ne se défassent pas tout seuls à la prochaine passe.
"""

from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .test_otp_vault import OtpCase, _chiffre_credible


@tagged('post_install', '-at_install')
class TestArchiveNestPasCorbeille(OtpCase):
    """🔴 Le défaut d'origine : `active` était la corbeille, et l'action
    « Archiver » générique d'Odoo écrivait dedans. Le mot promettait le
    contraire de ce qu'il faisait.
    """

    def test_active_est_readonly_donc_archiver_generique_est_debranche(self):
        """Le seul levier qui débranche l'action « Archiver » du client web.

        🔴 `list_controller.js` lit `archiveEnabled` dans les champs du MODÈLE,
        pas dans ceux de la vue : retirer `<field name="active"/>` de l'arbre
        n'y change RIEN. Seul `readonly` la fait disparaître. Si quelqu'un
        retire ce drapeau pour « pouvoir archiver depuis la liste », il remet
        exactement le défaut qu'on a corrigé, et cet essai doit tomber avant.
        """
        self.assertTrue(
            self.env['bf.otp.token']._fields['active'].readonly,
            "active doit rester readonly, sinon Odoo réoffre son action "
            "« Archiver » et elle envoie le token à la CORBEILLE.")

    def test_archiver_ne_touche_jamais_active(self):
        self._coffre(self.alice)
        tid = self._jeton(self.alice)
        Token = self.env['bf.otp.token'].with_user(self.alice)
        Token.archive_token(tid)
        jeton = self.env['bf.otp.token'].browse(tid)
        self.assertTrue(jeton.archived)
        self.assertTrue(jeton.archived_at)
        self.assertTrue(jeton.active, "archiver ne met rien à la corbeille")
        self.assertFalse(jeton.deleted_at)

    def test_desarchiver_efface_la_date(self):
        self._coffre(self.alice)
        tid = self._jeton(self.alice)
        Token = self.env['bf.otp.token'].with_user(self.alice)
        Token.archive_token(tid)
        Token.unarchive_token(tid)
        jeton = self.env['bf.otp.token'].browse(tid)
        self.assertFalse(jeton.archived)
        self.assertFalse(jeton.archived_at)

    def test_un_token_archive_reste_dans_la_liste_avec_son_drapeau(self):
        """⚠️ Le contrat des TROIS surfaces : une seule lecture, un drapeau.

        Filtrer l'archive ici obligerait chaque surface à un second appel pour
        chercher dans son archive, et une recherche qui ne retrouve pas ce
        qu'on a rangé soi-même fait regretter l'archivage.
        """
        self._coffre(self.alice)
        tid = self._jeton(self.alice)
        Token = self.env['bf.otp.token'].with_user(self.alice)
        Token.archive_token(tid)
        lignes = Token.load_my_tokens()
        self.assertEqual(len(lignes), 1)
        self.assertTrue(lignes[0]['archived'])

    def test_la_corbeille_sort_de_la_liste_meme_archivee(self):
        self._coffre(self.alice)
        tid = self._jeton(self.alice)
        Token = self.env['bf.otp.token'].with_user(self.alice)
        Token.archive_token(tid)
        Token.delete_token(tid)
        self.assertEqual(Token.load_my_tokens(), [])
        corbeille = Token.load_my_trash()
        self.assertEqual(len(corbeille), 1)
        self.assertTrue(corbeille[0]['archived'],
                        "la corbeille garde l'état d'archive, pour le rendre "
                        "intact à la restauration")

    def test_restaurer_rend_le_token_a_son_archive(self):
        self._coffre(self.alice)
        tid = self._jeton(self.alice)
        Token = self.env['bf.otp.token'].with_user(self.alice)
        Token.archive_token(tid)
        Token.delete_token(tid)
        Token.restore_token(tid)
        jeton = self.env['bf.otp.token'].browse(tid)
        self.assertTrue(jeton.active)
        self.assertTrue(jeton.archived, "restaurer ne dérange pas le rangement")

    def test_detruire_un_archive_qui_nest_pas_a_la_corbeille_est_refuse(self):
        """La destruction reste le SECOND geste, l'archive ne l'avance pas."""
        self._coffre(self.alice)
        tid = self._jeton(self.alice)
        Token = self.env['bf.otp.token'].with_user(self.alice)
        Token.archive_token(tid)
        with self.assertRaises(ValidationError):
            Token.purge_token(tid)

    def test_archiver_le_token_de_quelquun_dautre_est_refuse(self):
        self._coffre(self.alice)
        self._coffre(self.bob)
        tid = self._jeton(self.alice)
        with self.assertRaises(ValidationError):
            self.env['bf.otp.token'].with_user(self.bob).archive_token(tid)

    def test_set_archived_ignore_ce_qui_nest_pas_a_soi(self):
        """⚠️ Une liste d'identifiants n'est pas une autorisation."""
        self._coffre(self.alice)
        self._coffre(self.bob)
        a = self._jeton(self.alice)
        b = self._jeton(self.bob, name='bob@exemple.com')
        touches = self.env['bf.otp.token'].with_user(self.alice).set_archived(
            [a, b], True)
        self.assertEqual(touches, 1)
        self.assertTrue(self.env['bf.otp.token'].browse(a).archived)
        self.assertFalse(self.env['bf.otp.token'].browse(b).archived,
                         "le token de Bob ne doit pas bouger")

    def test_import_rapporte_letat_darchive(self):
        self._coffre(self.alice)
        res = self.env['bf.otp.token'].with_user(self.alice).import_tokens([{
            'name': 'range@exemple.com', 'issuer': 'Rangé', 'archived': True,
            'secret_cipher': _chiffre_credible(), 'secret_iv': 'aXYxMjM0NTY3ODkw',
        }])
        self.assertEqual(res['created'], 1)
        jeton = self.env['bf.otp.token'].search([('name', '=', 'range@exemple.com')])
        self.assertTrue(jeton.archived)
        self.assertTrue(jeton.archived_at)


@tagged('post_install', '-at_install')
class TestRegroupementPropose(OtpCase):
    """L'assistant propose ; il n'écrit que ce qu'on retient."""

    def _wizard(self, user, ctx=None):
        return self.env['bf.otp.group.wizard'].with_user(user).with_context(
            **(ctx or {})).create({})

    def test_le_domaine_se_lit_dans_le_nom_du_compte(self):
        W = self.env['bf.otp.group.wizard']
        self.assertEqual(W._domaine_du_compte('compte@exemple.com'), 'exemple.com')
        self.assertEqual(W._domaine_du_compte('Une Personne <p@Sous.Exemple.CA>'),
                         'sous.exemple.ca')
        self.assertEqual(W._domaine_du_compte('compte sans adresse'), '')
        self.assertEqual(W._domaine_du_compte(''), '')

    def test_les_paquets_viennent_du_domaine_et_le_gros_dabord(self):
        self._coffre(self.alice)
        for i in range(3):
            self._jeton(self.alice, name=f'a{i}@maison.test', issuer=f'Service {i}')
        self._jeton(self.alice, name='seul@ailleurs.test', issuer='Autre')
        w = self._wizard(self.alice)
        paquets = [(l.nom, l.nombre) for l in w.line_ids]
        self.assertEqual(paquets, [('maison.test', 3), ('ailleurs.test', 1)])
        self.assertEqual(w.total, 4)
        self.assertEqual(w.deja_range, 0)
        self.assertEqual(w.sans_proposition, 0)

    def test_sans_adresse_lemetteur_ne_sert_que_sil_est_partage(self):
        """🔴 Le compte des émetteurs se fait sur TOUT le coffre.

        Sinon la proposition changerait selon ce qu'on a sélectionné avant.
        """
        self._coffre(self.alice)
        self._jeton(self.alice, name='racine', issuer='Partagé')
        self._jeton(self.alice, name='autre', issuer='Partagé')
        self._jeton(self.alice, name='orphelin', issuer='Unique')
        w = self._wizard(self.alice)
        self.assertEqual([(l.nom, l.nombre) for l in w.line_ids],
                         [('Partagé', 2)])
        self.assertEqual(w.sans_proposition, 1,
                         "un émetteur seul ne fabrique pas un paquet de un")

    def test_ce_qui_porte_deja_une_etiquette_nest_ni_propose_ni_ecrit(self):
        self._coffre(self.alice)
        deja = self._jeton(self.alice, name='range@maison.test',
                           group_name='Mon rangement')
        self._jeton(self.alice, name='libre@maison.test')
        w = self._wizard(self.alice)
        self.assertEqual(w.deja_range, 1)
        self.assertEqual([(l.nom, l.nombre) for l in w.line_ids], [('maison.test', 1)])
        w.action_appliquer()
        self.assertEqual(self.env['bf.otp.token'].browse(deja).group_name,
                         'Mon rangement', "le rangement de quelqu'un ne se défait pas")

    def test_appliquer_ecrit_letiquette_retenue(self):
        self._coffre(self.alice)
        a = self._jeton(self.alice, name='un@maison.test')
        b = self._jeton(self.alice, name='deux@maison.test')
        w = self._wizard(self.alice)
        w.line_ids.nom = 'Blue Fox'
        w.action_appliquer()
        for tid in (a, b):
            self.assertEqual(self.env['bf.otp.token'].browse(tid).group_name,
                             'Blue Fox')

    def test_un_paquet_decoche_nest_pas_ecrit(self):
        self._coffre(self.alice)
        self._jeton(self.alice, name='un@maison.test')
        self._jeton(self.alice, name='deux@ailleurs.test')
        w = self._wizard(self.alice)
        garde = w.line_ids.filtered(lambda l: l.nom == 'maison.test')
        (w.line_ids - garde).retenu = False
        w.action_appliquer()
        self.assertTrue(self.env['bf.otp.token'].search([
            ('name', '=', 'un@maison.test')]).group_name)
        self.assertFalse(self.env['bf.otp.token'].search([
            ('name', '=', 'deux@ailleurs.test')]).group_name)

    def test_aucun_paquet_retenu_leve_plutot_que_de_ne_rien_faire(self):
        self._coffre(self.alice)
        self._jeton(self.alice, name='un@maison.test')
        w = self._wizard(self.alice)
        w.line_ids.retenu = False
        with self.assertRaises(UserError):
            w.action_appliquer()

    def test_un_paquet_retenu_sans_nom_leve(self):
        self._coffre(self.alice)
        self._jeton(self.alice, name='un@maison.test')
        w = self._wizard(self.alice)
        w.line_ids.nom = '   '
        with self.assertRaises(UserError):
            w.action_appliquer()

    def test_lassistant_ne_voit_que_le_coffre_de_qui_louvre(self):
        self._coffre(self.alice)
        self._coffre(self.bob)
        self._jeton(self.alice, name='alice@maison.test')
        self._jeton(self.bob, name='bob@maison.test')
        w = self._wizard(self.alice)
        self.assertEqual(w.total, 1)
        self.assertEqual([(l.nom, l.nombre) for l in w.line_ids], [('maison.test', 1)])

    def test_un_identifiant_etranger_glisse_dans_une_ligne_nest_pas_ecrit(self):
        """⚠️ Re-borner à l'ÉCRITURE, pas seulement à la proposition.

        L'assistant est un modèle transitoire et ses lignes sont modifiables
        par son propriétaire : un identifiant posé là ne doit pas ouvrir le
        coffre d'un autre.
        """
        self._coffre(self.alice)
        self._coffre(self.bob)
        self._jeton(self.alice, name='alice@maison.test')
        etranger = self._jeton(self.bob, name='bob@maison.test')
        w = self._wizard(self.alice)
        w.line_ids.sudo().write({'token_ids': [(4, etranger)]})
        w.action_appliquer()
        self.assertFalse(self.env['bf.otp.token'].browse(etranger).group_name,
                         "le token de Bob ne doit pas être rangé par Alice")

    def test_la_selection_borne_lassistant(self):
        self._coffre(self.alice)
        vise = self._jeton(self.alice, name='un@maison.test')
        self._jeton(self.alice, name='deux@ailleurs.test')
        w = self._wizard(self.alice, {
            'active_model': 'bf.otp.token', 'active_ids': [vise]})
        self.assertEqual(w.total, 1)
        self.assertEqual([l.nom for l in w.line_ids], ['maison.test'])
