"""Les surfaces client parlent la langue de qui les lit.

Révélé par un ordre du jour rédigé en anglais pour un prospect anglophone
(en_CA), parti avec objet, corps, PDF et nom de fichier en français. Deux défauts :

1. la langue ne regardait que le client du projet (« Prospection » n'en a pas),
   donc l'OdJ retombait sur la langue de l'organisateur ;
2. l'habillage était écrit en dur en français.

Règle arrêtée le 2026-10-16 : langue choisie à la main, sinon
client du projet, sinon lecteurs EXTERNES (unanimes : leur langue ; mélangés :
l'anglais), sinon la personne qui travaille, sinon la société.

Les essais épinglent fr_CA et en_CA, qu'ils activent : selon la base, en_US
est absente ou seule.
"""

from datetime import timedelta

from odoo import Command, fields
from odoo.tests import Form, TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestLangueDesLecteurs(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Lang = cls.env['res.lang']
        for code in ('fr_CA', 'en_CA'):
            Lang._activate_lang(code)
        cls.env['ir.module.module'].search([('name', '=', 'bf_meeting')])._load_module_terms(
            ['bf_meeting'], ['fr_CA'], overwrite=True)
        cls.env = cls.env(context=dict(cls.env.context, lang='fr_CA', tz='America/Toronto'))
        Partner = cls.env['res.partner']
        cls.lecteur_en = Partner.create({'name': 'Reader Essai', 'email': 'reader@essai.test', 'lang': 'en_CA'})
        cls.lectrice_fr = Partner.create({'name': 'Lectrice Essai', 'email': 'lectrice@essai.test', 'lang': 'fr_CA'})
        cls.anne = Partner.create({'name': 'Anne Essai', 'email': 'anne@essai.test', 'lang': 'en_CA'})
        cls.collegue = cls.env['res.users'].create({
            'name': 'Collègue Essai', 'login': 'collegue-25549@essai.test',
            'lang': 'fr_CA', 'groups_id': [Command.set([cls.env.ref('base.group_user').id])],
        }).partner_id
        cls.prospection = cls.env['project.project'].create({'name': 'Prospection essai'})
        cls.client_fr = Partner.create({'name': 'Client FR', 'is_company': True, 'lang': 'fr_CA'})
        cls.projet_client = cls.env['project.project'].create({
            'name': 'Projet client', 'partner_id': cls.client_fr.id})

    def _odj(self, lecteurs, projet=None, **vals):
        vals.setdefault('date', fields.Datetime.now() + timedelta(days=3))
        odj = self.env['meeting.agenda'].with_context(skip_auto_refine=True).create({
            'name': 'OdJ essai 25549',
            'project_id': (projet or self.prospection).id,
            'participant_ids': [Command.set(lecteurs.ids)],
            **vals,
        })
        # Fin de la requête de création : la langue calculée est stockée. Sans
        # ce vidage, elle ne se calculerait qu'au premier changement suivant
        # (un envoi, puis un participant), ce qui n'arrive jamais en vrai.
        self.env.flush_all()
        return odj

    # -- la règle ---------------------------------------------------------

    def test_lecteur_externe_anglophone_sans_client(self):
        """Prospection sans client : l'organisateur interne ne compte pas."""
        odj = self._odj(self.collegue | self.lecteur_en)
        self.assertEqual(odj.lang, 'en_CA')
        self.assertFalse(odj.lang_manual)

    def test_lecteurs_melanges_donnent_l_anglais(self):
        odj = self._odj(self.lecteur_en | self.lectrice_fr)
        self.assertEqual(odj.lang, 'en_CA')

    def test_lecteurs_unanimes_francophones(self):
        odj = self._odj(self.collegue | self.lectrice_fr)
        self.assertEqual(odj.lang, 'fr_CA')

    def test_un_interne_anglophone_ne_rend_pas_le_melange(self):
        """Les internes ne comptent pas : un collègue anglophone et une cliente
        francophone donnent le français, pas l'anglais du « mélange »."""
        collegue_en = self.env['res.users'].create({
            'name': 'Colleague EN', 'login': 'colleague-en-25549@essai.test', 'lang': 'en_CA',
            'groups_id': [Command.set([self.env.ref('base.group_user').id])]}).partner_id
        odj = self._odj(collegue_en | self.lectrice_fr)
        self.assertEqual(odj.lang, 'fr_CA')

    def test_seuls_les_internes_retombent_sur_la_personne(self):
        odj = self._odj(self.collegue).with_context(lang='en_CA')
        odj.invalidate_recordset(['lang'])
        self.assertEqual(odj._bf_langue_envoi(), odj.lang)

    def test_le_client_du_projet_passe_avant_les_lecteurs(self):
        odj = self._odj(self.lecteur_en, projet=self.projet_client)
        self.assertEqual(odj.lang, 'fr_CA')

    def test_la_langue_suit_les_lecteurs_ajoutes(self):
        odj = self._odj(self.lectrice_fr)
        self.assertEqual(odj.lang, 'fr_CA')
        odj.participant_ids = [Command.link(self.lecteur_en.id)]
        self.assertEqual(odj.lang, 'en_CA')

    def test_les_destinataires_comptent_aussi(self):
        odj = self._odj(self.lectrice_fr, recipient_ids=[Command.set(self.anne.ids)])
        self.assertEqual(odj.lang, 'en_CA')

    def test_le_choix_manuel_tient(self):
        odj = self._odj(self.lecteur_en)
        odj.lang = 'fr_CA'
        self.assertTrue(odj.lang_manual)
        odj.participant_ids = [Command.link(self.anne.id)]
        self.assertEqual(odj.lang, 'fr_CA')

    def test_le_formulaire_renvoie_la_langue_calculee_sans_la_figer(self):
        """Le formulaire écrit la langue que son calcul vient de changer, avec
        les participants : ce n'est pas un choix de l'usager."""
        odj = self._odj(self.lectrice_fr)
        odj.write({'participant_ids': [Command.link(self.lecteur_en.id)], 'lang': 'en_CA'})
        self.assertFalse(odj.lang_manual)
        odj.participant_ids = [Command.unlink(self.lecteur_en.id)]
        self.assertEqual(odj.lang, 'fr_CA')

    def test_le_formulaire_fige_une_langue_autre_que_la_regle(self):
        """Langue choisie et participant ajouté dans la même écriture : la langue
        écrite n'est pas celle que donne la règle, c'est un choix."""
        odj = self._odj(self.lectrice_fr)
        odj.write({'participant_ids': [Command.link(self.lecteur_en.id)], 'lang': 'fr_CA'})
        self.assertTrue(odj.lang_manual)
        self.assertEqual(odj.lang, 'fr_CA')

    def test_creer_avec_une_autre_langue_que_la_regle_la_fige(self):
        """Rendez-vous d'un demandeur, raffinage : la langue arrive écrite."""
        odj = self._odj(self.lectrice_fr, lang='en_CA')
        self.assertTrue(odj.lang_manual)
        self.assertEqual(odj.lang, 'en_CA')

    def test_au_formulaire_le_choix_tient_quand_on_ajoute_des_participants(self):
        """L'onchange ne protège que le champ de l'appel en cours : sans la marque
        posée au choix, le calcul remettait la langue des lecteurs."""
        odj = self._odj(self.lectrice_fr)
        with Form(odj) as formulaire:
            formulaire.lang = 'en_CA'
            formulaire.participant_ids.add(self.anne)
            formulaire.participant_ids.add(self.lectrice_fr)
        self.assertEqual(odj.lang, 'en_CA')
        self.assertTrue(odj.lang_manual)

    def test_au_formulaire_la_langue_suit_les_participants(self):
        odj = self._odj(self.lectrice_fr)
        with Form(odj) as formulaire:
            formulaire.participant_ids.add(self.lecteur_en)
        self.assertEqual(odj.lang, 'en_CA')
        self.assertFalse(odj.lang_manual)

    def test_un_ordre_du_jour_envoye_ne_change_plus_de_langue(self):
        odj = self._odj(self.lectrice_fr)
        odj.action_mark_sent_manually()
        odj.participant_ids = [Command.link(self.lecteur_en.id)]
        self.assertEqual(odj.lang, 'fr_CA')

    def test_un_compte_rendu_envoye_ne_change_plus_de_langue(self):
        cr = self.env['meeting.record'].create({
            'name': 'CR envoyé', 'date': fields.Datetime.now(), 'duration_minutes': 5,
            'project_id': self.prospection.id,
            'report_recipient_ids': [Command.set(self.lectrice_fr.ids)]})
        self.assertEqual(cr.lang, 'fr_CA')
        cr.report_state = 'sent'
        cr.report_recipient_ids = [Command.link(self.lecteur_en.id)]
        self.assertEqual(cr.lang, 'fr_CA')

    def test_la_langue_parlee_posee_par_le_traitement_tient(self):
        """Le meeting-processor crée le CR avec la langue de l'enregistrement,
        même quand elle égale la règle ; un destinataire ajouté ensuite ne la
        change pas."""
        cr = self.env['meeting.record'].create({
            'name': 'CR parlé', 'date': fields.Datetime.now(), 'duration_minutes': 5,
            'project_id': self.prospection.id, 'lang': 'fr_CA',
            'report_recipient_ids': [Command.set(self.lectrice_fr.ids)]})
        self.assertTrue(cr.lang_manual)
        cr.report_recipient_ids = [Command.link(self.lecteur_en.id)]
        self.assertEqual(cr.lang, 'fr_CA')

    def test_qui_modifie_ne_donne_pas_sa_langue(self):
        """Un OdJ interne créé en français ne bascule pas quand un collègue à
        l'interface anglaise (ou l'usager de l'API) lui ajoute un participant."""
        # Organisé par le collègue francophone : OdooBot, organisateur par défaut,
        # n'est en fr_CA que sur certaines bases.
        odj = self._odj(self.collegue, organizer_id=self.collegue.user_ids.id)
        self.assertEqual(odj.lang, 'fr_CA')
        autre = self.env['res.users'].create({
            'name': 'Collègue EN', 'login': 'collegue-en2-25549@essai.test', 'lang': 'en_CA',
            'groups_id': [Command.set([self.env.ref('base.group_user').id])]})
        chez_autre = odj.with_user(autre).with_context(lang='en_CA').sudo()
        chez_autre.participant_ids = [Command.link(autre.partner_id.id)]
        # Fin de SA requête : le recalcul se fait dans son environnement.
        chez_autre.env.flush_all()
        self.assertEqual(odj.lang, 'fr_CA')

    def test_retirer_la_marque_rend_la_langue_a_la_regle(self):
        odj = self._odj(self.lectrice_fr, lang='en_CA')
        self.assertTrue(odj.lang_manual)
        odj.lang_manual = False
        self.assertEqual(odj.lang, 'fr_CA')

    def test_dupliquer_ne_fige_pas(self):
        odj = self._odj(self.lectrice_fr)
        copie = odj.copy()
        self.assertFalse(copie.lang_manual)
        copie.participant_ids = [Command.link(self.lecteur_en.id)]
        self.assertEqual(copie.lang, 'en_CA')

    def test_creer_au_formulaire_ne_fige_pas(self):
        with Form(self.env['meeting.agenda'].with_context(skip_auto_refine=True)) as formulaire:
            formulaire.name = 'OdJ au formulaire'
            formulaire.project_id = self.prospection
            formulaire.date = fields.Datetime.now() + timedelta(days=3)
            formulaire.participant_ids.add(self.lecteur_en)
        odj = formulaire.record
        self.assertEqual(odj.lang, 'en_CA')
        self.assertFalse(odj.lang_manual)

    def test_une_langue_ecrite_seule_par_du_code_tient(self):
        """Le raffinage (XML-RPC) écrit la langue seule, même égale à la règle :
        c'est un choix ; un destinataire ajouté ensuite ne la change pas."""
        odj = self._odj(self.lectrice_fr)
        odj.write({'lang': 'fr_CA'})
        self.assertTrue(odj.lang_manual)
        odj.participant_ids = [Command.link(self.lecteur_en.id)]
        self.assertEqual(odj.lang, 'fr_CA')

    def test_le_compte_rendu_tire_de_l_odj_garde_sa_langue_sans_la_figer(self):
        odj = self._odj(self.lecteur_en)
        cr = self.env['meeting.record'].browse(odj.action_create_meeting_record()['res_id'])
        self.assertEqual(cr.lang, 'en_CA')
        self.assertFalse(cr.lang_manual)
        odj_fige = self._odj(self.lecteur_en, lang='fr_CA')
        cr = self.env['meeting.record'].browse(odj_fige.action_create_meeting_record()['res_id'])
        self.assertEqual(cr.lang, 'fr_CA')
        self.assertTrue(cr.lang_manual)

    def test_compte_rendu_lit_les_presences(self):
        cr = self.env['meeting.record'].create({
            'name': 'CR essai 25549', 'date': fields.Datetime.now(),
            'duration_minutes': 15, 'project_id': self.prospection.id})
        self.env['meeting.attendance'].create({'meeting_id': cr.id, 'partner_id': self.lecteur_en.id})
        self.assertEqual(cr.lang, 'en_CA')

    # -- les surfaces -----------------------------------------------------

    def _rendre(self, xmlid, record):
        gabarit = self.env.ref(xmlid)
        langue = gabarit._render_lang(record.ids)[record.id]
        gabarit = gabarit.with_context(lang=langue)
        return (gabarit._render_field('subject', record.ids)[record.id],
                str(gabarit._render_field('body_html', record.ids)[record.id]))

    def test_courriel_de_l_ordre_du_jour_dans_les_deux_langues(self):
        sujet, corps = self._rendre('bf_meeting.meeting_agenda_mail_template',
                                    self._odj(self.lecteur_en, objectives='Obj'))
        self.assertTrue(sujet.startswith('Agenda — '), sujet)
        self.assertIn('Hello,', corps)
        self.assertNotIn('Bonjour', corps)
        sujet, corps = self._rendre('bf_meeting.meeting_agenda_mail_template',
                                    self._odj(self.lectrice_fr, objectives='Obj'))
        self.assertTrue(sujet.startswith('Ordre du jour — '), sujet)
        self.assertIn('Bonjour,', corps)
        self.assertNotIn('Hello', corps)

    def test_courriel_du_compte_rendu_dans_les_deux_langues(self):
        for lecteur, attendu, interdit in ((self.lecteur_en, 'Meeting minutes — ', 'Compte rendu'),
                                           (self.lectrice_fr, 'Compte rendu — ', 'Meeting minutes')):
            cr = self.env['meeting.record'].create({
                'name': 'CR essai', 'date': fields.Datetime.now(), 'duration_minutes': 15,
                'project_id': self.prospection.id, 'summary': 'x',
                'report_recipient_ids': [Command.set(lecteur.ids)]})
            sujet, corps = self._rendre('bf_meeting.meeting_report_mail_template', cr)
            self.assertTrue(sujet.startswith(attendu), sujet)
            self.assertNotIn(interdit, corps)

    def test_le_pdf_suit_la_langue_du_document_pas_celle_de_qui_imprime(self):
        """Avant : intitulés dans la langue de la personne qui imprime."""
        odj = self._odj(self.lecteur_en, objectives='Obj')
        Report = self.env['ir.actions.report'].with_context(lang='fr_CA')
        html = Report._render_qweb_html('bf_meeting.action_report_meeting_agenda', odj.ids)[0].decode()
        self.assertIn('Planned duration', html)
        self.assertIn('Confidential document', html)
        self.assertNotIn('Durée prévue', html)
        odj_fr = self._odj(self.lectrice_fr, objectives='Obj')
        Report = self.env['ir.actions.report'].with_context(lang='en_CA')
        html = Report._render_qweb_html('bf_meeting.action_report_meeting_agenda', odj_fr.ids)[0].decode()
        self.assertIn('Durée prévue', html)
        self.assertNotIn('Planned duration', html)

    def test_le_pdf_du_compte_rendu_suit_la_langue_du_document(self):
        cr = self.env['meeting.record'].create({
            'name': 'CR essai', 'date': fields.Datetime.now(), 'duration_minutes': 15,
            'project_id': self.prospection.id, 'summary': 'x', 'lang': 'en_CA'})
        Report = self.env['ir.actions.report'].with_context(lang='fr_CA')
        html = Report._render_qweb_html('bf_meeting.action_report_meeting_record', cr.ids)[0].decode()
        self.assertIn('Meeting date', html)
        self.assertNotIn('Date de la réunion', html)
        cr.lang = 'fr_CA'
        Report = self.env['ir.actions.report'].with_context(lang='en_CA')
        html = Report._render_qweb_html('bf_meeting.action_report_meeting_record', cr.ids)[0].decode()
        self.assertIn('Date de la réunion', html)
        self.assertNotIn('Meeting date', html)

    def test_nom_du_fichier_pdf(self):
        odj = self._odj(self.lecteur_en, date='2026-11-02 15:00:00')
        self.assertEqual(odj._bf_nom_fichier_pdf(), 'Agenda_OdJ_essai_25549_2026-11-02')
        odj = self._odj(self.lectrice_fr, date='2026-11-02 15:00:00')
        self.assertEqual(odj._bf_nom_fichier_pdf(), 'OdJ_OdJ_essai_25549_2026-11-02')
        cr = self.env['meeting.record'].create({
            'name': 'CR', 'date': '2026-11-02 15:00:00', 'duration_minutes': 5,
            'project_id': self.prospection.id, 'lang': 'en_CA'})
        self.assertEqual(cr._bf_nom_fichier_pdf(), 'Minutes_meeting_2026-11-02')
        cr.lang = 'fr_CA'
        self.assertEqual(cr._bf_nom_fichier_pdf(), 'CR_reunion_2026-11-02')

    def test_separateur_du_fuseau(self):
        organisateur = self.env['res.users'].create({
            'name': 'Organisateur NZ', 'login': 'nz-25549@essai.test', 'tz': 'Pacific/Auckland',
            'groups_id': [Command.set([self.env.ref('base.group_user').id])]})
        odj = self._odj(self.lecteur_en, organizer_id=organisateur.id)
        en = str(odj.with_context(lang='en_CA').report_date_display_html())
        fr = str(odj.with_context(lang='fr_CA').report_date_display_html())
        self.assertIn('(Auckland: ', en)
        self.assertIn('(Auckland : ', fr)

    def test_colonne_source_dans_la_langue_de_lecture(self):
        odj = self._odj(self.lecteur_en)
        tache = self.env['project.task'].create({
            'name': 'Tâche à discuter', 'project_id': self.prospection.id,
            'bf_discuss_tag': 'next_project'})
        epinglee = self.env['project.task'].create({
            'name': 'Tâche épinglée', 'project_id': self.prospection.id,
            'bf_meeting_agenda_id': odj.id})
        odj.invalidate_recordset()
        for lang, tag, pin in (('en_CA', 'Next project meeting', 'Pinned'),
                               ('fr_CA', 'Prochaine rencontre projet', 'Épinglée')):
            sources = {t['id']: t['source'] for t in
                       odj.with_context(lang=lang)._get_report_data()['tagged_tasks']}
            if tache.id in sources:
                self.assertEqual(sources[tache.id], tag)
            self.assertEqual(sources[epinglee.id], pin)

    def test_encadre_des_changements_dans_les_deux_langues(self):
        odj = self._odj(self.lecteur_en, resend_include_changes=True)
        odj.action_mark_sent_manually()
        odj.participant_ids = [Command.link(self.anne.id)]
        self.env.invalidate_all()
        en = str(odj.with_context(lang='en_CA').resend_changes_block_html())
        fr = str(odj.with_context(lang='fr_CA').resend_changes_block_html())
        self.assertIn('added: Anne Essai', en)
        self.assertIn('ajouté : Anne Essai', fr)
        self.assertNotIn('ajouté', en)
