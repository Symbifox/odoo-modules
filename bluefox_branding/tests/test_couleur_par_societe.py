"""La couleur des boutons suit la société du dossier (18.0.3.30.0).

Ce qui doit tenir avant tout : deux courriels du même dossier portent la même
couleur, quelle que soit la session qui les crée et quel que soit l'usager du
cron qui les envoie. La session n'est que le dernier repli, après le dossier
puis l'auteur du message.

Ce qui doit tenir ensuite : une société qui n'a jamais reçu de couleur garde
le mauve d'Odoo dans le CORPS du courriel. C'est voulu — la société commande
jusqu'au bout, et le remède est de lui donner une couleur, pas de replier sur
une autre société.

🔴 Le bouton des avis, lui, se replie : là, le cœur peignait déjà une vraie
couleur (`email_secondary_color`), et la laisser tomber en mauve serait une
régression. « Sans accent » veut dire « resté sur un des mauves du cœur », pas
« champ vide » : `report_brand_primary` a un DÉFAUT, donc il n'est jamais vide.
"""
import re

from markupsafe import Markup

from odoo.tests import TransactionCase, tagged
from odoo.tools import is_html_empty

from ..models import brand_color_mixin as bc

MAUVE = '#714B67'
BOUTON = ('<table><tr><td style="background: %s;">'
          '<a href="#" style="color:#FFFFFF;">Voir</a></td></tr></table>')
# Le mauve d'Odoo s'écrit aussi en rgb() dans les corps du cœur, et le
# remplacement a une branche à lui pour cette forme (`OLD_COLORS`). Le corps
# ci-dessus n'en porte aucune : sans ce second corps, cette branche n'est
# éprouvée par rien.
MAUVE_RGB = 'rgb(135,90,123)'
BLEU_BF_RGB = 'rgb(41,171,226)'


@tagged('post_install', '-at_install')
class TestCouleurParSociete(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, mail_notify_force_send=False))
        Societe = cls.env['res.company']
        cls.soc_session = Societe.create({
            'name': 'Société de la session', 'report_brand_primary': '#0b5fa5'})
        cls.soc_dossier = Societe.create({
            'name': 'Société du dossier', 'report_brand_primary': '#c2410c'})
        cls.soc_auteur = Societe.create({
            'name': "Société de l'auteur", 'report_brand_primary': '#15803d'})
        cls.soc_sans_couleur = Societe.create({'name': 'Société sans couleur'})

        cls.dossier = cls.env['res.partner'].create({
            'name': 'Dossier de la filiale', 'email': 'dossier@essai.test',
            'company_id': cls.soc_dossier.id})
        cls.dossier_sans_societe = cls.env['res.partner'].create({
            'name': 'Dossier sans société', 'email': 'sans-societe@essai.test'})
        cls.dossier_sans_couleur = cls.env['res.partner'].create({
            'name': 'Dossier de la société sans couleur', 'email': 'sans-couleur@essai.test',
            'company_id': cls.soc_sans_couleur.id})

        cls.auteur = cls.env['res.users'].create({
            'name': "Auteur d'un avis", 'login': 'auteur-couleur-essai',
            'company_id': cls.soc_auteur.id,
            'company_ids': [(6, 0, cls.soc_auteur.ids)]})

    # -- outils -----------------------------------------------------------------

    def _courriel(self, valeurs, societe_de_session=None):
        """Un courriel créé depuis une session donnée (la société d'en-tête
        par défaut, celle qui n'a rien à voir avec le dossier)."""
        societe_de_session = societe_de_session or self.soc_session
        valeurs = dict({'subject': 'Essai', 'body_html': BOUTON % MAUVE}, **valeurs)
        return self.env['mail.mail'].with_company(societe_de_session).create(valeurs)

    def assertPorte(self, html, societe):
        self.assertIn(societe.report_brand_primary, html)

    def assertNePortePas(self, html, societe):
        self.assertNotIn(societe.report_brand_primary, html)

    def _modele_sans_table(self):
        """Un modèle du registre qui n'a AUCUNE table derrière lui.

        Les 229 modèles abstraits de cette base (mesuré) sont dans `env` et
        n'ont pas de relation : `account.move.send` en est un, et
        `mail.render.mixin` aussi — celui-là est présent partout où le module
        est présent, d'où le repli.
        """
        nom = 'account.move.send' if 'account.move.send' in self.env else 'mail.render.mixin'
        self.assertTrue(self.env[nom]._abstract,
                        f"{nom} a maintenant une table : la scène ne vise plus rien")
        return nom

    # -- la règle ---------------------------------------------------------------

    def test_le_dossier_commande(self):
        """Un courriel d'un dossier de la filiale porte la couleur de la filiale."""
        courriel = self._courriel({'model': 'res.partner', 'res_id': self.dossier.id})
        self.assertPorte(courriel.body_html, self.soc_dossier)
        self.assertNePortePas(courriel.body_html, self.soc_session)
        self.assertNotIn(MAUVE.lower(), courriel.body_html.lower())

    def test_le_dossier_passe_avant_l_auteur(self):
        """Dossier ET auteur connus, et de deux sociétés : le dossier gagne."""
        courriel = self._courriel({
            'model': 'res.partner', 'res_id': self.dossier.id,
            'author_id': self.auteur.partner_id.id})
        self.assertPorte(courriel.body_html, self.soc_dossier)
        self.assertNePortePas(courriel.body_html, self.soc_auteur)

    def test_sans_societe_au_dossier_c_est_l_auteur(self):
        """Le cas dominant : le dossier n'a pas de société, l'auteur en a une."""
        courriel = self._courriel({
            'model': 'res.partner', 'res_id': self.dossier_sans_societe.id,
            'author_id': self.auteur.partner_id.id})
        self.assertPorte(courriel.body_html, self.soc_auteur)
        self.assertNePortePas(courriel.body_html, self.soc_session)

    def test_l_auteur_compte_meme_archive(self):
        """Un robot d'intégration est souvent un usager archivé : il compte."""
        self.auteur.active = False
        courriel = self._courriel({'author_id': self.auteur.partner_id.id})
        self.assertPorte(courriel.body_html, self.soc_auteur)
        self.assertNePortePas(courriel.body_html, self.soc_session)

    def test_un_usager_actif_passe_devant_un_usager_archive(self):
        """Deux usagers sur le même contact, un actif, un archivé : l'actif décide.

        ⚠️ Mesuré au banc : lu sous `active_test=False`, `user_ids` rend
        l'ordre de la base, pas un ordre de préférence. `res.users._order`
        vaut « name, login » et les usagers d'un même contact partagent son
        nom, donc le login départage — l'archivé, nommé pour passer premier,
        l'emportait. L'archivé compte quand il est SEUL (l'essai précédent le
        garde), il ne passe pas devant.

        Trois sociétés distinctes, trois couleurs : l'échec dit lequel des
        deux défauts est revenu, l'archivé ou la session.
        """
        soc_archive = self.env['res.company'].create({
            'name': "Société de l'usager archivé", 'report_brand_primary': '#7e22ce'})
        contact = self.env['res.partner'].create({'name': 'Contact à deux usagers'})
        archive = self.env['res.users'].create({
            'login': 'a-archive-couleur-essai', 'partner_id': contact.id,
            'company_id': soc_archive.id, 'company_ids': [(6, 0, soc_archive.ids)]})
        archive.active = False
        self.env['res.users'].create({
            'login': 'z-actif-couleur-essai', 'partner_id': contact.id,
            'company_id': self.soc_auteur.id,
            'company_ids': [(6, 0, self.soc_auteur.ids)]})
        self.assertEqual(
            contact.with_context(active_test=False).user_ids[0], archive,
            "la scène ne tient plus : l'archivé n'est plus lu en premier")

        courriel = self._courriel({'author_id': contact.id})
        self.assertPorte(courriel.body_html, self.soc_auteur)
        self.assertNePortePas(courriel.body_html, soc_archive)
        self.assertNePortePas(courriel.body_html, self.soc_session)

    def test_sans_dossier_ni_auteur_c_est_la_session(self):
        courriel = self._courriel({})
        self.assertPorte(courriel.body_html, self.soc_session)

    def test_l_avis_lit_le_dossier_par_son_message(self):
        """Un avis ne reçoit que l'identifiant de son message : le dossier s'y lit."""
        message = self.dossier.message_post(body='Corps', message_type='notification',
                                            subtype_xmlid='mail.mt_note')
        courriel = self._courriel({'mail_message_id': message.id})
        self.assertPorte(courriel.body_html, self.soc_dossier)
        self.assertNePortePas(courriel.body_html, self.soc_session)

    def test_la_societe_notee_sur_le_message_ne_fait_pas_foi(self):
        """`record_company_id` porte la session gelée : le dossier vivant gagne."""
        message = self.dossier.message_post(body='Corps', message_type='notification',
                                            subtype_xmlid='mail.mt_note')
        message.sudo().record_company_id = self.soc_session
        courriel = self._courriel({'mail_message_id': message.id})
        self.assertPorte(courriel.body_html, self.soc_dossier)
        self.assertNePortePas(courriel.body_html, self.soc_session)

    def test_un_dossier_sans_societe_ne_herite_pas_de_la_session_gelee(self):
        """Le cœur écrit `env.company` sur un message sans dossier : l'auteur passe devant."""
        message = self.dossier_sans_societe.message_post(
            body='Corps', message_type='notification', subtype_xmlid='mail.mt_note',
            author_id=self.auteur.partner_id.id)
        self.assertEqual(message.record_company_id, self.env.company,
                         "le cœur a changé : il ne gèle plus la société de la session")
        courriel = self._courriel({'mail_message_id': message.id})
        self.assertPorte(courriel.body_html, self.soc_auteur)

    def test_une_societe_sans_couleur_garde_le_mauve(self):
        courriel = self._courriel({'model': 'res.partner',
                                   'res_id': self.dossier_sans_couleur.id})
        self.assertIn(MAUVE, courriel.body_html)
        self.assertNePortePas(courriel.body_html, self.soc_session)

    # -- les trois chemins ------------------------------------------------------

    def test_l_envoi_ne_prend_pas_la_societe_du_cron(self):
        """Le cron de la file tourne sous une autre société : le corps ne bouge pas.

        Le mauve est remis en base sans passer par l'ORM, pour mesurer `_send`
        seul : une écriture ordinaire serait déjà corrigée par `write`.
        """
        courriel = self._courriel({'model': 'res.partner', 'res_id': self.dossier.id},
                                  societe_de_session=self.soc_dossier)
        self.env.cr.execute('UPDATE mail_mail SET body_html = %s WHERE id = %s',
                            (BOUTON % MAUVE, courriel.id))
        courriel.invalidate_recordset(['body_html'])
        courriel.with_company(self.soc_session)._send()
        self.assertPorte(courriel.body_html, self.soc_dossier)
        self.assertNePortePas(courriel.body_html, self.soc_session)

    def test_l_ecriture_suit_le_dossier(self):
        courriel = self._courriel({'model': 'res.partner', 'res_id': self.dossier.id,
                                   'body_html': '<p>Sans bouton</p>'})
        courriel.with_company(self.soc_session).write({'body_html': BOUTON % MAUVE})
        self.assertPorte(courriel.body_html, self.soc_dossier)
        self.assertNePortePas(courriel.body_html, self.soc_session)

    def test_une_ecriture_en_lot_donne_a_chacun_sa_couleur(self):
        """Deux courriels, deux dossiers, une seule écriture."""
        un = self._courriel({'model': 'res.partner', 'res_id': self.dossier.id,
                             'body_html': '<p>Sans bouton</p>'})
        deux = self._courriel({'model': 'res.partner',
                               'res_id': self.dossier_sans_couleur.id,
                               'body_html': '<p>Sans bouton</p>'})
        (un + deux).with_company(self.soc_session).write({'body_html': BOUTON % MAUVE})
        self.assertPorte(un.body_html, self.soc_dossier)
        self.assertIn(MAUVE, deux.body_html)

    def test_le_gabarit_rendu_suit_chaque_dossier(self):
        """Un seul rendu, deux dossiers, deux couleurs."""
        rendu = self.env['mail.render.mixin'].with_company(self.soc_session)._render_template(
            BOUTON % MAUVE, 'res.partner',
            [self.dossier.id, self.dossier_sans_societe.id])
        self.assertPorte(rendu[self.dossier.id], self.soc_dossier)
        self.assertPorte(rendu[self.dossier_sans_societe.id], self.soc_session)

    def test_envoi_reel_par_gabarit(self):
        """Le chemin complet : mail.template rendu, mis en page, mis en file."""
        gabarit = self.env['mail.template'].create({
            'name': 'Essai couleur',
            'model_id': self.env.ref('base.model_res_partner').id,
            'subject': 'Essai', 'body_html': BOUTON % MAUVE,
            'partner_to': '{{ object.id }}',
            'email_layout_xmlid': 'bluefox_branding.bf_mail_layout'})
        gabarit.with_company(self.soc_session).send_mail(self.dossier.id, force_send=False)
        courriel = self.env['mail.mail'].search(
            [('recipient_ids', 'in', self.dossier.ids)], order='id desc', limit=1)
        self.assertPorte(courriel.body_html, self.soc_dossier)
        self.assertNePortePas(courriel.body_html, self.soc_session)

    # -- le bouton des avis du cœur ---------------------------------------------

    def _avis_du_coeur(self, societe, gabarit='mail.mail_notification_layout'):
        """La mise en page des avis d'Odoo, rendue pour une société donnée.

        ⚠️ Deux pièges du contexte, les deux mesurés en écrivant cet essai :
        la vue appelle `is_html_empty(...)` et `author_user._is_internal()`, et
        un nom suivi d'une parenthèse qui manque au contexte lève un KeyError
        au lieu de rendre vide ; et le bouton est gardé par `show_header`, donc
        sans `email_notification_force_header` le rendu sort SANS bouton — un
        corps de 974 octets qui se lit comme « la couleur est fausse » alors
        qu'il n'y a pas de couleur du tout.
        """
        return self.env['mail.render.mixin']._render_encapsulate(
            gabarit, Markup('<p>CORPS</p>'), add_context={
                'message': self.env['mail.message'].sudo().new(
                    dict(body=Markup('<p>CORPS</p>'), record_name='Dossier')),
                'model_description': 'Dossier',
                'company': societe,
                'has_button_access': True,
                'email_notification_force_header': True,
                'button_access': {'url': 'https://exemple.test/voir', 'title': 'Voir'},
                'is_html_empty': is_html_empty,
                'author_user': self.env['res.users'],
                'subtype': self.env['mail.message.subtype'].sudo(),
            })

    def _fond_du_bouton(self, html):
        trouve = re.search(
            r'border-radius: 3px; text-align: center; background: (#[0-9a-fA-F]{3,6})',
            html)
        return (trouve.group(1) or '').lower() if trouve else None

    def test_le_bouton_d_un_avis_porte_l_accent_de_la_marque(self):
        """Et non `email_secondary_color`, le champ d'Odoo que nul n'entretient."""
        self.soc_dossier.email_secondary_color = '#123456'
        self.assertEqual(self._fond_du_bouton(self._avis_du_coeur(self.soc_dossier)),
                         self.soc_dossier.report_brand_primary.lower())

    def test_au_defaut_du_champ_le_bouton_se_replie_sur_le_champ_d_odoo(self):
        """Une société qui n'a jamais choisi d'accent : le champ d'Odoo décide.

        🔴 L'essai d'avant posait `report_brand_primary: False`, un état que le
        DÉFAUT du champ rend impossible (`#714B67`, posé par
        `bf_onboarding_base`) : il validait une branche morte, et le repli
        qu'il prétendait garder était inatteignable — une société restée au
        défaut passait par l'accent, donc par le mauve. La scène est donc
        celle du parc : on ne pose PAS le champ, et l'essai vérifie d'abord
        qu'il porte bien le mauve par défaut.
        """
        societe = self.env['res.company'].create(
            {'name': 'Société au défaut du champ', 'email_secondary_color': '#123456'})
        self.assertEqual(societe.report_brand_primary, MAUVE,
                         "le défaut du champ a changé : la scène ne vise plus rien")
        self.assertEqual(self._fond_du_bouton(self._avis_du_coeur(societe)), '#123456')

    def test_un_accent_au_defaut_ne_se_lit_pas_comme_un_accent_choisi(self):
        """Deux sociétés, le même champ d'Odoo, un seul accent choisi.

        🔴 C'est la régression mesurée par la relecture adverse, dans ses
        propres couleurs : une société restée au défaut dont le champ d'Odoo
        porte `#000000` aurait vu son bouton passer du NOIR au mauve d'Odoo,
        parce qu'un champ qui a un défaut n'est jamais vide. Elle garde son
        noir ; sa voisine, qui a choisi un accent, porte le sien.
        """
        au_defaut = self.env['res.company'].create(
            {'name': 'Filiale restée au défaut', 'email_secondary_color': '#000000'})
        choisie = self.env['res.company'].create(
            {'name': 'Filiale qui a choisi', 'report_brand_primary': '#176CF2',
             'email_secondary_color': '#000000'})
        self.assertEqual(self._fond_du_bouton(self._avis_du_coeur(au_defaut)), '#000000')
        self.assertEqual(self._fond_du_bouton(self._avis_du_coeur(choisie)), '#176cf2')

    def test_le_defaut_se_reconnait_a_la_casse_et_aux_espaces_pres(self):
        """Les deux mauves du cœur comptent, quelle que soit leur écriture.

        `report_brand_primary` est un `Char` nu, sans contrainte : il s'écrit à
        la main, par RPC ou par import. Un `#714b67` en minuscules, entouré
        d'espaces, ou le `#875A7B` de la mise en page sont le même non-choix.
        """
        for accent in (MAUVE, MAUVE.lower(), '  %s  ' % MAUVE, '#875A7B', '#875a7b'):
            with self.subTest(accent=accent):
                societe = self.env['res.company'].create(
                    {'name': 'Société écrite à la main (%s)' % accent,
                     'report_brand_primary': accent,
                     'email_secondary_color': '#123456'})
                self.assertEqual(
                    self._fond_du_bouton(self._avis_du_coeur(societe)), '#123456')

    def test_sans_rien_le_bouton_garde_le_mauve_d_odoo(self):
        """Au défaut du champ ET sans champ d'Odoo : le mauve, en dernier recours."""
        societe = self.env['res.company'].create(
            {'name': 'Société sans rien', 'email_secondary_color': False})
        self.assertEqual(societe.report_brand_primary, MAUVE,
                         "le défaut du champ a changé : la scène ne vise plus rien")
        self.assertEqual(self._fond_du_bouton(self._avis_du_coeur(societe)), '#875a7b')

    def test_une_societe_vide_ne_leve_rien_et_rend_le_mauve(self):
        """La mise en page peut recevoir une société VIDE (le cœur lui-même
        l'admet : `company and not company.uses_default_logo`). La décision
        rend alors le mauve d'Odoo au lieu de lever — une couleur ne vaut
        jamais une trace d'erreur, et c'est la vue entière qui tomberait.
        """
        vide = self.env['res.company']
        self.assertEqual(vide._brand_email_button_color(), '#875A7B')
        self.assertEqual(self._fond_du_bouton(self._avis_du_coeur(vide)), '#875a7b')

    def test_les_mises_en_page_heritees_suivent(self):
        """La surcharge vise la vue mère : ses héritières en profitent."""
        self.soc_dossier.email_secondary_color = '#123456'
        html = self._avis_du_coeur(
            self.soc_dossier,
            gabarit='mail.mail_notification_layout_with_responsible_signature')
        self.assertEqual(self._fond_du_bouton(html),
                         self.soc_dossier.report_brand_primary.lower())

    # -- les gardes -------------------------------------------------------------

    def test_un_dossier_disparu_ne_leve_rien(self):
        """Un `res_id` qui ne désigne plus rien : la chaîne se replie.

        ⚠️ Ce cas ne s'éprouve que sur la chaîne, pas de bout en bout, et les
        deux raisons ont été mesurées au banc : `mail.message` REFUSE un
        `res_id` qui ne désigne rien (MissingError), et effacer le dossier
        efface aussi ses messages (`mail.thread.unlink`), donc le courriel en
        file part avec eux (`_inherits` en cascade). La garde reste là pour un
        modèle sans fil de discussion et pour un effacement hors ORM.
        """
        societe = self.env['mail.mail']._bf_societe_de_marque(
            {'model': 'res.partner', 'res_id': 2 ** 31 - 1})
        self.assertEqual(societe, self.env.company)

    def test_un_modele_inconnu_ne_leve_rien(self):
        """Un modèle désinstallé depuis la mise en file : la chaîne se replie."""
        societe = self.env['mail.mail']._bf_societe_de_marque(
            {'model': 'modele.qui.n.existe.pas', 'res_id': 1})
        self.assertEqual(societe, self.env.company)

    def test_un_modele_sans_table_ne_leve_rien(self):
        """Un modèle ABSTRAIT : la chaîne se replie, elle ne lève pas.

        🔴 `model in env` ne dit PAS qu'il y a une table, et c'est le seul
        endroit où ce lot pouvait lever. Mesuré : sans la garde,
        `{'model': 'account.move.send', 'res_id': 1}` rend `UndefinedTable`
        (`SELECT … FROM account_move_send`, une relation qui n'existe pas) —
        et PostgreSQL abandonne alors toute la transaction, donc le
        `message_post` qui précède et tout ce qui suit. Une couleur ne vaut
        jamais ça.
        """
        societe = self.env['mail.mail']._bf_societe_de_marque(
            {'model': self._modele_sans_table(), 'res_id': 1})
        self.assertEqual(societe, self.env.company)

    def test_le_rendu_sur_un_modele_sans_table_ne_leve_rien(self):
        """Le jumeau de la garde, sur le second chemin.

        ⚠️ Comme le dossier disparu, cela ne s'éprouve que sur la chaîne, et
        la raison est mesurée au banc : le rendu du cœur tombe AVANT le lot
        sur un modèle abstrait, parce que
        `_render_template_inline_template_regex` lit `record.id` et qu'un
        modèle abstrait n'a pas d'`id`. La garde reste là pour deux raisons —
        elle appelle le même `bc.modele_sans_table` que `mail.mail`, où le
        chemin complet existe bel et bien, et un `UndefinedTable` ici
        coûterait la transaction entière, pas seulement une couleur.
        """
        self.assertEqual(
            self.env['mail.render.mixin']._bf_societes_des_dossiers(
                self._modele_sans_table(), [1]),
            {})

    def test_un_accent_malforme_ne_fait_pas_lever_le_remplacement(self):
        """Une couleur qui ressemble à un code de gabarit ne casse rien.

        🔴 Mesuré : passé en gabarit de remplacement à `re.sub`, un accent
        valant `'#29ABE2\\'` lève `re.error: bad escape` et `'\\1'`
        `invalid group reference` — en pleine création de courriel.
        `report_brand_primary` est un `Char` nu, sans contrainte, écrivable
        par RPC ou par import, et depuis ce lot la couleur peut venir de
        n'importe quelle société de la chaîne, y compris une filiale que
        l'opérateur ne voit pas. La couleur ressort LITTÉRALE : laide, pas
        levée. `\\g<0>` est là pour le dire autrement — en gabarit il
        remettrait le mauve, donc l'essai verrait aussi un remplacement
        redevenu gabarit sans lever.
        """
        # 🔴 Ce que la branche rgb() rend pour chacun, MESURÉ et non déduit :
        # `#29ABE2\\` garde ses trois paires lisibles (la barre oblique inverse
        # tombe APRÈS le sixième caractère), donc elle se dérive normalement —
        # elle n'est malformée que pour `re.sub`. Seuls `\\1` et `\\g<0>`
        # atteignent le repli, et ce repli doit être le mauve d'ODOO, jamais
        # l'accent de Blue Fox.
        en_rgb_attendu = {'#29ABE2\\': BLEU_BF_RGB, '\\1': MAUVE_RGB,
                          '\\g<0>': MAUVE_RGB, '#29ABE2': BLEU_BF_RGB}
        for accent in ('#29ABE2\\', '\\1', '\\g<0>', '#29ABE2'):
            with self.subTest(accent=accent):
                self.soc_dossier.report_brand_primary = accent
                courriel = self._courriel({'model': 'res.partner',
                                           'res_id': self.dossier.id})
                self.assertIn(accent, courriel.body_html)
                self.assertNotIn(MAUVE, courriel.body_html)

                # 🔴 La branche rgb() du remplacement, que le corps ci-dessus
                # n'atteint pas : elle dérive l'accent en `rgb()` et tombe sur
                # un repli quand il n'est pas lisible. Ce repli valait
                # l'accent de BLUE FOX — un module qui sert huit bases aurait
                # mis la marque de Blue Fox dans le bouton d'un autre
                # locataire. Il vaut maintenant le mauve d'Odoo, c'est-à-dire
                # la couleur qu'on remplace : le corps ressort tel quel.
                en_rgb = self._courriel({'model': 'res.partner',
                                         'res_id': self.dossier.id,
                                         'body_html': BOUTON % MAUVE_RGB})
                self.assertIn(en_rgb_attendu[accent], en_rgb.body_html)
                if en_rgb_attendu[accent] == MAUVE_RGB:
                    self.assertNotIn(BLEU_BF_RGB, en_rgb.body_html)

    def test_un_identifiant_illisible_ne_leve_rien(self):
        for indices in ({'model': 'res.partner', 'res_id': 'pas un entier'},
                        {'model': 'res.partner', 'res_id': False},
                        {'mail_message_id': 'pas un entier'},
                        {'author_id': None}):
            self.assertEqual(self.env['mail.mail']._bf_societe_de_marque(indices),
                             self.env.company, indices)

    def test_un_corps_sans_mauve_n_est_pas_touche(self):
        corps = '<p>Rien à repeindre</p>'
        courriel = self._courriel({'model': 'res.partner', 'res_id': self.dossier.id,
                                   'body_html': corps})
        self.assertEqual(courriel.body_html, corps)

    def test_la_lecture_de_la_societe_ne_demande_aucun_droit(self):
        """L'usager du cron de la file ne voit pas la filiale, et la couleur sort.

        `mail.mail` n'est ouvert qu'au groupe Configuration : l'usager l'a, mais
        il n'a que la société de session dans ses sociétés permises, et la règle
        multi-société de `res.partner` lui cache donc le dossier de la filiale.
        Sans la lecture en `sudo`, ce n'est pas une mauvaise couleur qui sort,
        c'est une AccessError pendant l'envoi — et le courriel finirait en
        « exception » plutôt que chez son destinataire.

        ⚠️ La même scène à la CRÉATION ne se joue pas : les droits de
        `mail.message` se lisent sur le document visé, donc cet usager ne peut
        même pas créer le courriel (mesuré au banc).
        """
        usager = self.env['res.users'].create({
            'name': 'Usager de la seule société de session',
            'login': 'session-seule-essai',
            'company_id': self.soc_session.id,
            'company_ids': [(6, 0, self.soc_session.ids)],
            'groups_id': [(6, 0, [self.env.ref('base.group_system').id])]})
        courriel = self._courriel({'model': 'res.partner', 'res_id': self.dossier.id,
                                   'body_html': '<p>Sans bouton</p>'})
        # 🔴 Vider le cache, sinon l'essai est AVEUGLE : la création a déjà lu
        # `company_id` du dossier en superusager, et le cache de l'ORM le
        # resservirait sans contrôler les droits. Mesuré par la passe de
        # mutation : sans cette ligne, retirer le `sudo` du lot laisse l'essai
        # VERT. Lecture directe mesurée : `dossier.with_user(usager).company_id`
        # lève bien AccessError (la règle multi-société de res.partner cache un
        # contact dont `partner_share` est vrai).
        self.dossier.invalidate_recordset(['company_id'])
        # C'est l'usager du cron de la file qui envoie, pas l'auteur : il lit un
        # courriel qui porte sur un dossier qu'il ne voit pas.
        societe = courriel.with_user(usager).with_company(
            self.soc_session)._bf_societe_de_marque_du_courriel()
        self.assertEqual(societe, self.soc_dossier)

    def test_le_rendu_ne_demande_aucun_droit_non_plus(self):
        """Le second chemin lit la société du dossier avec les mêmes égards."""
        usager = self.env['res.users'].create({
            'name': 'Usager du rendu', 'login': 'rendu-seul-essai',
            'company_id': self.soc_session.id,
            'company_ids': [(6, 0, self.soc_session.ids)],
            'groups_id': [(6, 0, [self.env.ref('base.group_user').id])]})
        self.dossier.invalidate_recordset(['company_id'])
        rendu = self.env['mail.render.mixin'].with_user(usager).with_company(
            self.soc_session)._render_template(
                BOUTON % MAUVE, 'res.partner', [self.dossier.id])
        self.assertPorte(rendu[self.dossier.id], self.soc_dossier)

    def test_le_html_sur_reste_du_html_sur(self):
        """Markup entre, Markup sort : sinon QWeb échappe tout le corps."""
        rendu = bc.replace_button_colors(self.env, Markup(BOUTON % MAUVE),
                                         company=self.soc_dossier)
        self.assertIsInstance(rendu, Markup)
        self.assertPorte(rendu, self.soc_dossier)

    def test_le_html_sur_traverse_le_rendu_d_un_gabarit(self):
        """Le même Markup, mais par `_render_template` — le chemin réel.

        C'est là que le type coûte cher : la mise en page du publipostage
        reverse le corps rendu dans un `<t t-out="body"/>`, et QWeb
        échapperait TOUT le corps si le lot rendait une chaîne nue — le
        destinataire lirait `&lt;table&gt;` au lieu de son courriel. L'essai
        d'à côté éprouve le remplacement seul ; celui-ci éprouve la méthode
        que le lot a restructurée.

        ⚠️ Moteur `qweb`, et c'est le cœur qui l'impose : mesuré,
        `_render_template_qweb_regex` rend du `Markup`, alors que
        `inline_template` rend une chaîne nue quoi qu'on lui donne. La garde
        se pose donc sur le moteur qui a quelque chose à perdre.
        """
        rendu = self.env['mail.render.mixin'].with_company(self.soc_session)._render_template(
            Markup(BOUTON % MAUVE), 'res.partner', [self.dossier.id], engine='qweb')
        self.assertIsInstance(rendu[self.dossier.id], Markup)
        self.assertPorte(rendu[self.dossier.id], self.soc_dossier)
