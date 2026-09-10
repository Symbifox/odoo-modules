"""Échange de comptes rendus entre locataires Symbifox.

Ce que ces tests gardent, ce sont trois promesses qui ne se voient pas à la
lecture du code :

* **la charge ne porte que ce que le PDF montre** : un jour quelqu'un ajoutera
  un champ au compte rendu, et rien dans le code ne l'empêchera de le verser
  dans l'export ;
* **l'import ne crée que le compte rendu** : pas de contact, pas de tâche, et
  surtout pas une notification à des gens qui n'ont rien demandé ;
* **le fichier est de l'entrée non fiable** : il arrive par courriel, et les
  formes tordues qu'il peut prendre cassent des choses très loin du fichier
  (un calculé STOCKÉ, un `t-foreach` de gabarit PDF).
"""

import base64
import json

from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged

from ..models.meeting_exchange import EXCHANGE_FORMAT, MAX_FILE_BYTES, html_to_lines


@tagged('post_install', '-at_install')
class TestMeetingExchange(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Exchange = cls.env['meeting.exchange']
        cls.premiere = cls.env['res.partner'].create({
            'name': 'Première Personne', 'email': 'premiere@essai.test'})
        cls.deuxieme = cls.env['res.partner'].create({
            'name': 'Deuxième Personne', 'email': 'deuxieme@essai.test'})
        cls.tierce = cls.env['res.partner'].create({
            'name': 'Tierce Personne', 'email': 'tierce@ailleurs.test'})
        cls.project = cls.env['project.project'].create({'name': 'Projet Échange'})
        cls.record = cls.env['meeting.record'].create({
            'name': 'Statutaire du 24 août 2026',
            'room_name': 'Statutaire',
            'date': '2026-08-24 17:00:00',
            'duration_minutes': 75,
            'location': 'Visioconférence',
            'project_id': cls.project.id,
            'summary': "Séance de travail.",
            'verbatim': 'TRANSCRIPTION-BRUTE ' * 100,
            'review_notes': 'NOTE-DE-REVISION-INTERNE',
            'report_state': 'reviewed',
            'structured_notes_json': json.dumps({
                'topics': [{'title': 'SECTION-PERSONNELLE',
                            'points': ['Un détail privé.']}],
                'open_questions': [{'question': 'Salaire ou dividende ?'}],
                'deliverables': [{'description': 'Chiffrier des dépenses'}],
            }, ensure_ascii=False),
            'attendance_ids': [
                Command.create({'partner_id': cls.premiere.id, 'status': 'present',
                                'role': 'animateur'}),
                Command.create({'partner_id': cls.deuxieme.id, 'status': 'present'}),
                Command.create({'partner_id': cls.tierce.id, 'status': 'excused'}),
            ],
            'decision_ids': [
                Command.create({'sequence': 10, 'name': "Fin d'exercice au 31 août",
                                'decision_maker_id': cls.deuxieme.id}),
            ],
            'topic_ids': [
                Command.create({'sequence': 10, 'name': '[Section personnelle retirée]',
                                'points_html': '<ul><li>Retirée.</li></ul>'}),
                Command.create({'sequence': 20, 'name': "Fin d'exercice",
                                'points_html': '<ul><li>Tranchée au <b>31 août</b>.</li></ul>'}),
            ],
        })

    def _payload(self):
        return self.Exchange.build_payload(self.record)

    def _parsed(self, payload=None):
        raw = json.dumps(payload or self._payload(), ensure_ascii=False)
        return self.Exchange.parse_payload(raw.encode('utf-8'))

    # ── ce qui n'a pas le droit de voyager ────────────────────────────────────

    def test_charge_sans_verbatim_ni_notes_internes(self):
        """Le verbatim et les notes de révision ne sortent pas du bâtiment."""
        raw = json.dumps(self._payload(), ensure_ascii=False)
        self.assertNotIn('TRANSCRIPTION-BRUTE', raw)
        self.assertNotIn('NOTE-DE-REVISION-INTERNE', raw)

    def test_charge_sans_balisage(self):
        """Aucune balise ne traverse : le HTML des sujets est réduit en lignes.

        C'est ce qui rend inutile toute question d'assainissement à l'import.
        """
        raw = json.dumps(self._payload(), ensure_ascii=False)
        for fragment in ('<ul>', '<li>', '<b>', '<script', '<p>'):
            self.assertNotIn(fragment, raw)
        points = self._payload()['meeting']['topics'][1]['points']
        # une balise EN LIGNE ne laisse pas d'espace derrière elle
        self.assertEqual(points, ['Tranchée au 31 août.'])

    def test_les_sujets_viennent_de_la_representation_MODIFIABLE(self):
        """Tout le dessin tient sur ce point.

        `topic_ids` et `structured_notes_json` portent tous deux les sujets, et
        SEUL le premier est modifiable depuis la fiche. Sur la production de
        un parc réel, 20 des 183 comptes rendus qui portent les deux les ont
        divergents, et deux de ces écarts sont des retraits volontaires de
        détails personnels que la version JSON avait gardés. Exporter depuis le
        JSON renverrait donc au client ce qu'on venait d'en retirer.
        """
        raw = json.dumps(self._payload(), ensure_ascii=False)
        self.assertNotIn('SECTION-PERSONNELLE', raw)
        self.assertIn('[Section personnelle retirée]', raw)

    def test_repli_sur_le_json_quand_il_n_y_a_pas_de_sujets_modele(self):
        """103 des 288 comptes rendus d'un parc réel n'ont aucun `meeting.topic`."""
        self.record.topic_ids.unlink()
        titres = [t['title'] for t in self._payload()['meeting']['topics']]
        self.assertEqual(titres, ['SECTION-PERSONNELLE'])

    def test_preference_de_societe_portee_a_la_creation(self):
        """La case suit le réglage de société, y compris hors interface.

        Elle ne peut pas le faire par le défaut du champ : celui-ci est
        évalué quand Odoo pose la colonne sur les lignes existantes, avant que
        la colonne de `res.company` livrée par le même module existe. Vu sur
        une base qui a des données ; une base neuve ne l'atteint jamais.
        """
        self.env.company.meeting_exchange_default = True
        rec = self.env['meeting.record'].create({'name': 'Avec préférence'})
        self.assertTrue(rec.exchange_include_json)

        self.env.company.meeting_exchange_default = False
        rec2 = self.env['meeting.record'].create({'name': 'Sans préférence'})
        self.assertFalse(rec2.exchange_include_json)

    def test_valeur_explicite_prime_sur_la_preference(self):
        self.env.company.meeting_exchange_default = True
        rec = self.env['meeting.record'].create({
            'name': 'Explicite', 'exchange_include_json': False})
        self.assertFalse(rec.exchange_include_json)

    def test_defaut_du_champ_ne_lit_aucune_autre_table(self):
        """Garde-fou sur la cause exacte : le défaut doit être une constante.

        Un appelable y remet la lecture de `res.company` au moment de la
        migration, et la mise à jour repart en échec sur toute base qui porte
        des comptes rendus.
        """
        # Odoo enveloppe TOUT défaut dans un appelable : tester `callable`
        # ne discrimine rien. Ce qui discrimine, c'est ce qu'il rend quand la
        # société dit oui.
        self.env.company.meeting_exchange_default = True
        defaut = self.env['meeting.record']._fields['exchange_include_json'].default
        self.assertFalse(
            defaut(self.env['meeting.record']),
            "le défaut du champ ne doit pas lire res.company")

    def test_piece_jointe_absente_si_la_case_est_decochee(self):
        self.record.exchange_include_json = False
        self.assertFalse(self.record._bf_exchange_attachment())

    def test_piece_jointe_regeneree_et_non_empilee(self):
        """Deux envois ne laissent pas deux fichiers sur la fiche."""
        self.record.exchange_include_json = True
        first = self.record._bf_exchange_attachment()
        self.record.summary = 'Résumé corrigé'
        second = self.record._bf_exchange_attachment()
        self.assertEqual(first, second)
        self.assertIn('Résumé corrigé', second.raw.decode('utf-8'))
        self.assertEqual(self.env['ir.attachment'].search_count([
            ('res_model', '=', 'meeting.record'),
            ('res_id', '=', self.record.id),
            ('mimetype', '=', 'application/json'),
        ]), 1)

    def test_export_par_un_role_restreint_avec_element_de_matrice(self):
        """🔴 Le défaut trouvé sur un parc réel, pas au banc.

        Une décision peut pointer un élément de matrice de connaissances, et la
        charge en cite le NOM, comme le PDF. Un gestionnaire de rencontres qui
        n'est pas dans le groupe de la matrice ne peut pas lire ce modèle :
        sans sudo sur les libellés, l'export lève `AccessError`. Sur un parc
        réel, 132 des 288 comptes rendus portent un élément de matrice ; une
        base de démonstration n'en porte aucun, et la branche
        n'est jamais atteinte.
        """
        matrice = self.env['project.knowledge.matrix'].create({
            'name': 'Matrice essai', 'project_id': self.project.id})
        section = self.env['project.knowledge.section'].create({
            'name': 'Section essai', 'code': 'ES'})
        item = self.env['project.knowledge.item'].create({
            'name': 'Élément confidentiel', 'matrix_id': matrice.id,
            'section_id': section.id, 'decision_id': 'ES1'})
        self.record.decision_ids[0].knowledge_item_id = item

        restreint = self.env['res.users'].create({
            'name': 'Gestionnaire rencontres', 'login': 'gest-rencontres@essai.invalid',
            'groups_id': [Command.set([
                self.env.ref('base.group_user').id,
                self.env.ref('bf_meeting.group_meeting_manager').id,
            ])],
        })
        charge = self.env['meeting.exchange'].with_user(restreint).build_payload(
            self.record.with_user(restreint))
        self.assertEqual(charge['meeting']['decisions'][0]['knowledge_item'],
                         'Élément confidentiel')

    # ── validation : le fichier est de l'entrée non fiable ────────────────────

    def test_refus_fichier_non_json(self):
        with self.assertRaises(UserError):
            self.Exchange.parse_payload(b'ceci n est pas du json')

    def test_refus_mauvais_format(self):
        with self.assertRaises(UserError):
            self.Exchange.parse_payload(b'{"format": "autre.chose", "version": 1}')

    def test_refus_version_future(self):
        payload = self._payload()
        payload['version'] = 99
        with self.assertRaises(UserError):
            self.Exchange.parse_payload(json.dumps(payload).encode())

    def test_refus_fichier_trop_gros(self):
        with self.assertRaises(UserError):
            self.Exchange.parse_payload(b'x' * (MAX_FILE_BYTES + 1))

    def test_refus_liste_remplacee_par_une_chaine(self):
        """Le piège central : une chaîne là où une liste est attendue.

        Elle ne lève rien : elle s'itère caractère par caractère. Mesuré sur le
        gabarit de rendu : 100 ko de texte rendaient 1 Mo de HTML, dans un champ
        calculé STOCKÉ.
        """
        payload = self._payload()
        payload['meeting']['topics'] = 'pas une liste'
        with self.assertRaises(UserError):
            self.Exchange.parse_payload(json.dumps(payload).encode())

    def test_refus_puces_donnees_comme_une_chaine(self):
        """La garde que le test précédent NE prouve PAS.

        Mettre une chaîne à la place de la liste de sujets lève de toute façon,
        parce que chaque caractère n'est pas un objet, donc ce test-là passe
        même si la garde `_list` disparaît. Là où elle est la seule défense,
        c'est sur une liste de CHAÎNES : `"abc"` s'y itère en trois puces,
        et 100 ko de texte en 100 000 puces.
        """
        payload = self._payload()
        payload['meeting']['topics'][0]['points'] = 'abcdef'
        with self.assertRaises(UserError):
            self.Exchange.parse_payload(json.dumps(payload).encode())

    def test_refus_questions_ouvertes_donnees_comme_une_chaine(self):
        payload = self._payload()
        payload['meeting']['open_questions'] = 'abcdef'
        with self.assertRaises(UserError):
            self.Exchange.parse_payload(json.dumps(payload).encode())

    def test_refus_puce_qui_n_est_pas_du_texte(self):
        payload = self._payload()
        payload['meeting']['topics'][0]['points'] = [{'x': 1}]
        with self.assertRaises(UserError):
            self.Exchange.parse_payload(json.dumps(payload).encode())

    def test_refus_champ_texte_remplace_par_un_objet(self):
        payload = self._payload()
        payload['meeting']['name'] = {'nom': 'rusé'}
        with self.assertRaises(UserError):
            self.Exchange.parse_payload(json.dumps(payload).encode())

    def test_statut_de_presence_inconnu_retombe_sur_present(self):
        payload = self._payload()
        payload['meeting']['roster'][0]['status'] = 'ADMIN'
        parsed = self.Exchange.parse_payload(json.dumps(payload).encode())
        self.assertEqual(parsed['meeting']['roster'][0]['status'], 'present')

    def test_troncature_annoncee(self):
        payload = self._payload()
        payload['meeting']['name'] = 'x' * 9000
        parsed = self.Exchange.parse_payload(json.dumps(payload).encode())
        self.assertTrue(parsed['warnings'])

    # ── reprise ───────────────────────────────────────────────────────────────

    def test_import_ne_cree_que_le_compte_rendu(self):
        """Ni contact, ni tâche, ni courriel, ni notification, ni message."""
        parsed = self._parsed()
        avant = {
            model: self.env[model].search_count([])
            for model in ('res.partner', 'project.task', 'mail.mail',
                          'mail.notification', 'mail.message')
        }
        record = self.Exchange.apply_payload(parsed)
        self.assertTrue(record.exists())
        for model, count in avant.items():
            self.assertEqual(
                self.env[model].search_count([]), count,
                "l'import a créé un %s" % model)

    def test_apply_payload_refuse_un_simple_usager(self):
        """La garde ne peut pas vivre seulement dans l'assistant.

        `meeting.exchange` est un modèle ABSTRAIT : pas de table, donc
        `ir.model.access` n'est jamais consulté, et ses méthodes publiques
        restent appelables par `call_kw`. L'ACL de l'assistant ne protège que
        l'assistant.
        """
        simple = self.env['res.users'].create({
            'name': 'Usager rencontres', 'login': 'usager-rencontres@essai.invalid',
            'groups_id': [Command.set([
                self.env.ref('base.group_user').id,
                self.env.ref('bf_meeting.group_meeting_user').id,
            ])],
        })
        parsed = self._parsed()
        with self.assertRaises(AccessError):
            self.env['meeting.exchange'].with_user(simple).apply_payload(parsed)

    def test_import_n_apparie_que_les_contacts_existants(self):
        """La tierce personne n'a pas de fiche ici : elle est nommée, pas créée.

        Le locataire receveur ne connaît pas la même adresse. C'est la
        situation mesurée : sur les 59 adresses présentes aux rencontres de
        un parc réel, 15 seulement existent aussi chez un autre locataire.
        """
        payload = self._payload()
        for entry in payload['meeting']['roster']:
            if entry['name'] == 'Tierce Personne':
                entry['email'] = 'inconnu-ici@ailleurs.example'
        record = self.Exchange.apply_payload(self._parsed(payload))
        noms = record.attendance_ids.mapped('partner_id.name')
        self.assertEqual(sorted(noms), ['Deuxième Personne', 'Première Personne'])
        self.assertIn('Tierce Personne', record.exchange_received_html)
        self.assertFalse(self.env['res.partner'].search(
            [('email', '=', 'inconnu-ici@ailleurs.example')]))

    def test_import_entre_en_brouillon_sans_destinataire(self):
        """L'invariant du portail client.

        `bf_meeting_portal` ouvre un compte rendu au portail dès que
        `report_state == 'sent'`, que `report_sent_date` est renseignée et que
        le partenaire figure aux destinataires. Une copie importée qui
        recopierait l'état d'envoi s'afficherait dans le portail des clients de
        celui qui l'a importée.
        """
        self.record.write({
            'report_state': 'sent',
            'report_sent_date': '2026-08-25 12:00:00',
            'report_recipient_ids': [Command.set([self.deuxieme.id])],
        })
        record = self.Exchange.apply_payload(self._parsed())
        self.assertEqual(record.report_state, 'draft')
        self.assertFalse(record.report_sent_date)
        self.assertFalse(record.report_recipient_ids)

    def test_import_pose_la_provenance(self):
        parsed = self._parsed()
        record = self.Exchange.apply_payload(parsed)
        self.assertEqual(record.exchange_source_ref,
                         '%s:%s' % (self.env.cr.dbname, self.record.id))

    def test_import_rend_les_sujets_dans_les_DEUX_representations(self):
        """Une copie importée ne doit pas naître avec l'écart qu'on vient
        d'éviter à l'export."""
        record = self.Exchange.apply_payload(self._parsed())
        depuis_modele = record.topic_ids.mapped('name')
        depuis_json = [
            t['title']
            for t in json.loads(record.structured_notes_json)['topics']
        ]
        self.assertEqual(depuis_modele, depuis_json)

    def test_import_echappe_le_contenu_hostile(self):
        payload = self._payload()
        payload['meeting']['topics'][0]['title'] = '<script>alert(1)</script>'
        payload['meeting']['roster'][2]['name'] = '<img src=x onerror=alert(1)>'
        record = self.Exchange.apply_payload(
            self.Exchange.parse_payload(json.dumps(payload).encode()))
        for html in (record.notes_html or '', record.exchange_received_html or '',
                     record.topic_ids[0].points_html or ''):
            self.assertNotIn('<script', str(html))
            self.assertNotIn('onerror', str(html))

    def test_import_sans_verbatim(self):
        record = self.Exchange.apply_payload(self._parsed())
        self.assertFalse(record.verbatim)
        self.assertFalse(record.verbatim_html)

    def test_aller_retour_conserve_le_contenu(self):
        record = self.Exchange.apply_payload(self._parsed())
        record.exchange_include_json = True
        aller = self._payload()['meeting']
        retour = self.Exchange.build_payload(record)['meeting']
        self.assertEqual(aller['topics'], retour['topics'])
        self.assertEqual(aller['summary'], retour['summary'])
        self.assertEqual(aller['open_questions'], retour['open_questions'])
        self.assertEqual([d['name'] for d in aller['decisions']],
                         [d['name'] for d in retour['decisions']])

    # ── durcissement des gabarits de rendu ────────────────────────────────────

    def test_notes_structurees_tordues_ne_cassent_pas_la_fiche(self):
        """Le calculé stocké `notes_html` traverse ce champ.

        Il est aussi écrit par XML-RPC (pont, meeting-processor). Une valeur
        qui n'est pas un objet JSON y levait `AttributeError` DANS le calcul,
        ce qui rendait la fiche entièrement illisible.
        """
        for valeur in ('"une chaine"', '[1, 2, 3]', '42', '{"topics": "abc"}',
                       '{"topics": {"a": 1}}', 'pas du json'):
            self.record.structured_notes_json = valeur
            self.record.invalidate_recordset()
            self.assertIsNotNone(self.record.notes_html)
            self.assertIsNotNone(self.record.read(['notes_html'])[0])

    def test_pas_d_amplification_par_iteration_de_chaine(self):
        """Une chaîne à la place d'une liste de puces rendait un `<li>` par
        caractère : facteur 10 mesuré, dans un champ stocké."""
        self.record.structured_notes_json = json.dumps({
            'topics': [{'title': 'T', 'points': 'x' * 5000}]})
        self.record.invalidate_recordset()
        self.assertLess(len(self.record.notes_html or ''), 200)

    def test_donnees_du_rapport_pdf_resistent(self):
        """`_get_report_data` alimente des `t-foreach` du gabarit PDF."""
        self.record.structured_notes_json = json.dumps({'topics': 'abc',
                                                        'deliverables': 'def'})
        data = self.record._get_report_data()
        self.assertEqual(data['topics'], [])
        self.assertEqual(data['deliverables'], [])

    # ── assistant ─────────────────────────────────────────────────────────────

    def _wizard(self, raw=None):
        raw = raw or json.dumps(self._payload(), ensure_ascii=False).encode()
        return self.env['meeting.exchange.import.wizard'].create({
            'file': base64.b64encode(raw), 'filename': 'cr.json'})

    def test_assistant_verifie_avant_de_creer(self):
        wizard = self._wizard()
        avant = self.env['meeting.record'].search_count([])
        wizard.action_check()
        self.assertEqual(wizard.state, 'preview')
        self.assertEqual(self.env['meeting.record'].search_count([]), avant)
        self.assertIn('Provenance', wizard.preview_html)

    def test_assistant_refuse_un_envoi_trop_gros_avant_de_decoder(self):
        """Le plafond se lit sur le base64, pas après le décodage."""
        wizard = self.env['meeting.exchange.import.wizard'].create({
            'file': base64.b64encode(b'x' * (MAX_FILE_BYTES + 4096)),
            'filename': 'gros.json'})
        with self.assertRaises(UserError):
            wizard.action_check()

    def test_apercu_compte_comme_l_import(self):
        """Une personne citée deux fois ne devient pas « non appariée ».

        L'aperçu et la reprise partagent la même boucle ; deux boucles qui se
        ressemblent finissent par diverger, et c'est l'aperçu qui mentirait.
        """
        payload = self._payload()
        roster = payload['meeting']['roster']
        roster.append(dict(roster[0]))  # la même personne, citée deux fois
        parsed = self.Exchange.parse_payload(json.dumps(payload).encode())
        _, non_apparies, _ = self.Exchange._split_roster(parsed['meeting']['roster'])
        record = self.Exchange.apply_payload(parsed)
        self.assertNotIn(roster[0]['name'],
                         [e['name'] for e in non_apparies])
        self.assertEqual(len(record.attendance_ids), 3)

    def test_assistant_signale_le_doublon(self):
        self._wizard().action_import()
        wizard = self._wizard()
        wizard.action_check()
        self.assertTrue(wizard.duplicate_id)

    def test_assistant_remet_a_zero_au_changement_de_fichier(self):
        wizard = self._wizard()
        wizard.action_check()
        wizard.file = base64.b64encode(b'{}')
        wizard._onchange_file_reset()
        self.assertEqual(wizard.state, 'upload')
        self.assertFalse(wizard.preview_html)

    def test_assistant_pose_le_projet_choisi(self):
        wizard = self._wizard()
        wizard.project_id = self.project
        action = wizard.action_import()
        record = self.env['meeting.record'].browse(action['res_id'])
        self.assertEqual(record.project_id, self.project)

    # ── réduction du HTML ─────────────────────────────────────────────────────

    def test_html_to_lines(self):
        self.assertEqual(
            html_to_lines('<ul><li>Un</li><li>Deux</li></ul>'), ['Un', 'Deux'])
        self.assertEqual(
            html_to_lines('<ul><li>au <b>31 août</b>.</li></ul>'), ['au 31 août.'])
        self.assertEqual(
            html_to_lines('<ul><li>Le <script>alert(1)</script> texte.</li></ul>'),
            ['Le texte.'])
        self.assertEqual(html_to_lines('<p>Un</p><p>Deux</p>'), ['Un', 'Deux'])
        self.assertEqual(html_to_lines('Sans balise'), ['Sans balise'])
        self.assertEqual(html_to_lines(False), [])

    def test_format_declare_dans_le_fichier(self):
        self.assertEqual(self._payload()['format'], EXCHANGE_FORMAT)
