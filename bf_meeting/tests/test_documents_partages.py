"""Les documents de l'onglet partent avec le courriel et le PDF.

L'onglet « Documents » d'un ordre du jour ou d'un compte rendu n'était repris
nulle part : un OdJ partait sans les liens que l'organisateur avait préparés.

Les promesses :
* **ce qui part** : les pièces de l'onglet, et elles seules — pas celles du
  chatter, ni la copie d'échange, ni une pièce posée hors de l'onglet ;
* **quand** : la fenêtre décide (OdJ : avant ; compte rendu : après ;
  « pendant » jamais) et la pièce doit être visible au moment du rendu ;
* **comment** : un lien web tel quel, un fichier par un lien à jeton qu'un
  destinataire sans compte peut ouvrir, et le même jeton d'un envoi à l'autre ;
* **partout** : courriel (toutes langues) et PDF, de l'OdJ comme du compte
  rendu ; un renvoi d'OdJ dit quels documents ont bougé ;
* **la migration** marque les pièces d'avant sans y mêler le reste, et
  porte la section dans les traductions du courriel.
"""

import base64
import importlib.util
import json
import re
from datetime import timedelta
from pathlib import Path

from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.tests import Form, TransactionCase, tagged

PNG_1PX = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM"
    "IQAAAABJRU5ErkJggg=="
)
LIEN = 'https://nuage.essai.test/s/PresentationDuModule'
FICHIER = base64.b64encode(b'contenu du pre-read').decode()


def _load_migration():
    path = (Path(__file__).resolve().parent.parent
            / "migrations" / "18.0.3.72.0" / "post-migrate.py")
    spec = importlib.util.spec_from_file_location("bf_meeting_mig_3_72_0", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _quand(**delta):
    return fields.Datetime.to_string(fields.Datetime.now() + timedelta(**delta))


@tagged('post_install', '-at_install')
class TestDocumentsPartages(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Le bandeau du PDF appelle image_data_uri() sans garde.
        cls.env.company.meeting_logo = PNG_1PX
        cls.env['res.lang']._activate_lang('fr_CA')
        cls.env['ir.module.module']._load_module_terms(['bf_meeting'], ['fr_CA'], overwrite=True)
        cls.env = cls.env(context=dict(cls.env.context, lang='fr_CA', skip_auto_refine=True))
        cls.project = cls.env['project.project'].create({'name': 'Projet des documents'})
        cls.alice = cls.env['res.partner'].create({
            'name': 'Alice Exemple', 'email': 'alice@essai.test'})

    # -- outils ------------------------------------------------------------

    def _odj(self, **delta):
        return self.env['meeting.agenda'].create({
            'name': 'OdJ des documents',
            'project_id': self.project.id,
            'participant_ids': [Command.set(self.alice.ids)],
            'date': _quand(**(delta or {'days': 3})),
            'lang': 'fr_CA',
        })

    def _cr(self, **delta):
        return self.env['meeting.record'].create({
            'name': 'Compte rendu des documents',
            'project_id': self.project.id,
            'date': _quand(**(delta or {'days': -1})),
            'lang': 'fr_CA',
        })

    def _ajouter(self, rec, name, **vals):
        """Ajouter un document comme le fait l'onglet (commande x2many)."""
        vals.setdefault('url', LIEN)
        rec.write({'meeting_attachment_ids': [Command.create(dict(vals, name=name))]})
        return self.env['ir.attachment'].search([
            ('res_model', '=', rec._name), ('res_id', '=', rec.id), ('name', '=', name)])

    def _noms(self, rec):
        return sorted(d['name'] for d in rec._bf_shared_document_links())

    def _courriel(self, rec, lang='fr_CA'):
        xmlid = ('bf_meeting.meeting_agenda_mail_template' if rec._name == 'meeting.agenda'
                 else 'bf_meeting.meeting_report_mail_template')
        template = self.env.ref(xmlid)
        return template.with_context(lang=lang)._render_field('body_html', rec.ids)[rec.id]

    def _pdf(self, rec):
        report = ('bf_meeting.action_report_meeting_agenda' if rec._name == 'meeting.agenda'
                  else 'bf_meeting.action_report_meeting_record')
        html = self.env['ir.actions.report']._render_qweb_html(report, rec.ids)[0]
        return html.decode() if isinstance(html, bytes) else html

    # -- ce qui part -------------------------------------------------------

    def test_l_onglet_marque_ses_pieces(self):
        odj = self._odj()
        doc = self._ajouter(odj, 'Présentation')
        self.assertTrue(doc.bf_meeting_document)
        cr = self.env['meeting.record'].create({
            'name': 'CR créé avec un document', 'date': _quand(days=-1),
            'meeting_attachment_ids': [Command.create({'name': 'Dès la création', 'url': LIEN})],
        })
        pieces = self.env['ir.attachment'].search([
            ('res_model', '=', 'meeting.record'), ('res_id', '=', cr.id)])
        self.assertEqual(pieces.mapped('bf_meeting_document'), [True])

    def test_une_piece_hors_onglet_ne_part_pas(self):
        odj = self._odj()
        self._ajouter(odj, "De l'onglet")
        self.env['ir.attachment'].create({
            'name': 'Posée à côté', 'type': 'url', 'url': LIEN,
            'res_model': 'meeting.agenda', 'res_id': odj.id})
        odj.message_post(body='Pièce au chatter', attachments=[('chatter.txt', b'chatter')])
        self.assertEqual(self._noms(odj), ["De l'onglet"])

    def test_la_copie_d_echange_ne_part_pas_deux_fois(self):
        cr = self._cr()
        cr.exchange_include_json = True
        echange = cr._bf_exchange_attachment()
        self.assertTrue(echange, "précondition : la copie d'échange existe")
        self._ajouter(cr, 'Livrable')
        self.assertEqual(self._noms(cr), ['Livrable'])

    def test_l_interface_coche_un_ajout_a_l_onglet(self):
        """Le client web envoie toutes les colonnes d'une ligne neuve, la case
        comprise : sans le défaut de l'onglet, elle partait décochée."""
        for rec in (self._odj(), self._cr()):
            with self.subTest(model=rec._name):
                with Form(rec) as form:
                    with form.meeting_attachment_ids.new() as ligne:
                        ligne.name = 'Ajouté à l\'écran'
                        ligne.type = 'url'
                        ligne.url = LIEN
                self.assertEqual(self._noms(rec), ["Ajouté à l'écran"])

    def test_une_ligne_fichier_sans_fichier_ne_part_pas(self):
        odj = self._odj()
        self._ajouter(odj, 'Fichier oublié', url=False, type='binary')
        self.assertEqual(self._noms(odj), [])

    def test_decoche_il_reste_dans_l_onglet_sans_partir(self):
        odj = self._odj()
        doc = self._ajouter(odj, 'Notes internes', bf_meeting_document=False)
        odj.invalidate_recordset(['meeting_attachment_ids'])
        self.assertIn(doc, odj.meeting_attachment_ids)
        self.assertEqual(self._noms(odj), [])
        odj.write({'meeting_attachment_ids': [Command.update(doc.id, {'bf_meeting_document': True})]})
        self.assertEqual(self._noms(odj), ['Notes internes'])

    def test_un_fichier_depose_au_chatter_ne_part_que_coche(self):
        """Le trombone du chatter pose le fichier sur la fiche, sans message."""
        odj = self._odj()
        depot = self.env['ir.attachment'].create({
            'name': 'Soumission.pdf', 'datas': FICHIER,
            'res_model': 'meeting.agenda', 'res_id': odj.id})
        # Champ calculé sans dépendance : relu comme le fait l'interface.
        odj.invalidate_recordset(['meeting_attachment_ids'])
        self.assertIn(depot, odj.meeting_attachment_ids, "l'onglet l'affiche")
        self.assertEqual(self._noms(odj), [])
        odj.write({'meeting_attachment_ids': [Command.update(depot.id, {'bf_meeting_document': True})]})
        self.assertEqual(self._noms(odj), ['Soumission.pdf'])

    def test_seul_un_lien_web_part(self):
        odj = self._odj()
        self._ajouter(odj, 'Script', url='javascript:alert(1)')
        self._ajouter(odj, 'Transfert', url='ftp://serveur.essai.test/fichier')
        self._ajouter(odj, 'Avec espaces', url='  ' + LIEN + '  ')
        self._ajouter(odj, 'En clair', url='http://intranet.essai.test/page')
        liens = {d['name']: d['url'] for d in odj._bf_shared_document_links()}
        self.assertEqual(liens, {'Avec espaces': LIEN,
                                 'En clair': 'http://intranet.essai.test/page'})

    # -- quand -------------------------------------------------------------

    def test_odj_avant_la_rencontre(self):
        """Rencontre dans une heure : la fenêtre « pendant » (± 2 h) est déjà
        ouverte, elle ne part pourtant pas avec l'OdJ."""
        odj = self._odj(hours=1)
        for fenetre in ('always', 'pre', 'during', 'post'):
            self._ajouter(odj, fenetre, bf_visibility_window=fenetre)
        self._ajouter(odj, 'custom ouverte', bf_visibility_window='custom')
        self._ajouter(odj, 'custom échue', bf_visibility_window='custom',
                      bf_visible_until=_quand(hours=-1))
        self.assertEqual(self._noms(odj), ['always', 'custom ouverte', 'pre'])

    def test_odj_envoye_apres_la_rencontre(self):
        """Envoi tardif : « après » est visible, mais n'est pas un document d'OdJ ;
        le pre-read est échu."""
        odj = self._odj(hours=-3)
        for fenetre in ('always', 'pre', 'during', 'post'):
            self._ajouter(odj, fenetre, bf_visibility_window=fenetre)
        self.assertEqual(self._noms(odj), ['always'])

    def test_compte_rendu_apres_la_rencontre(self):
        """Rencontre il y a une heure : « pendant » est encore ouverte, elle ne
        part pas avec le compte rendu."""
        cr = self._cr(hours=-1)
        for fenetre in ('always', 'pre', 'during', 'post'):
            self._ajouter(cr, fenetre, bf_visibility_window=fenetre)
        self.assertEqual(self._noms(cr), ['always', 'post'])

    def test_compte_rendu_redige_d_avance(self):
        """Fenêtre « avant » encore ouverte : ce n'est pas un document de compte rendu."""
        cr = self._cr(days=2)
        self._ajouter(cr, 'pre', bf_visibility_window='pre')
        self._ajouter(cr, 'always')
        self.assertEqual(self._noms(cr), ['always'])

    # -- comment -----------------------------------------------------------

    def test_un_fichier_part_par_un_lien_a_jeton(self):
        odj = self._odj()
        doc = self._ajouter(odj, 'Pre-read', url=False, datas=FICHIER)
        self.assertEqual(doc.type, 'binary')
        self.assertFalse(doc.access_token, "précondition : aucun jeton avant le rendu")
        (lien,) = odj._bf_shared_document_links()
        self.assertTrue(doc.access_token)
        self.assertEqual(
            lien['url'],
            '%s/web/content/%s?access_token=%s&download=true' % (
                odj.get_base_url(), doc.id, doc.access_token))
        self.assertEqual(odj._bf_shared_document_links()[0]['url'], lien['url'],
                         "un renvoi donne le même lien")

    def test_le_jeton_ouvre_le_fichier_a_un_visiteur(self):
        """Le chemin du contrôleur `/web/content`, sous l'utilisateur public."""
        odj = self._odj()
        doc = self._ajouter(odj, 'Pre-read', url=False, datas=FICHIER,
                            bf_visibility_window='pre')
        (lien,) = odj._bf_shared_document_links()
        jeton = re.search(r'access_token=([^&]+)', lien['url']).group(1)
        public = self.env.ref('base.public_user')
        binary = self.env['ir.binary'].with_user(public)
        trouve = binary._find_record(res_model='ir.attachment', res_id=doc.id,
                                     access_token=jeton)
        self.assertEqual(trouve.raw, b'contenu du pre-read')
        with self.assertRaises(UserError):
            binary._find_record(res_model='ir.attachment', res_id=doc.id,
                                access_token='mauvais-jeton')

    # -- partout -----------------------------------------------------------

    def test_courriel_de_l_odj(self):
        odj = self._odj()
        self._ajouter(odj, 'Présentation du module')
        for lang in ('fr_CA', 'en_US'):
            with self.subTest(lang=lang):
                body = self._courriel(odj, lang)
                self.assertIn('>Documents</h3>', body)
                self.assertIn('href="%s"' % LIEN, body)
                self.assertIn('Présentation du module', body)

    def test_courriel_du_compte_rendu(self):
        cr = self._cr()
        self._ajouter(cr, 'Livrable final')
        for lang in ('fr_CA', 'en_US'):
            with self.subTest(lang=lang):
                body = self._courriel(cr, lang)
                self.assertIn('>Documents</h3>', body)
                self.assertIn('href="%s"' % LIEN, body)
                self.assertIn('Livrable final', body)

    def test_sans_document_sans_section(self):
        odj, cr = self._odj(), self._cr()
        self._ajouter(odj, 'Hors fenêtre', bf_visibility_window='post')
        for rec in (odj, cr):
            with self.subTest(model=rec._name):
                self.assertNotIn('>Documents</h3>', self._courriel(rec))
                self.assertNotRegex(self._pdf(rec), r'section-title">\s*Documents\s*<')

    def test_le_nom_est_echappe(self):
        odj = self._odj()
        self._ajouter(odj, '<b>Gras</b> & co')
        body = self._courriel(odj)
        self.assertIn('&lt;b&gt;Gras&lt;/b&gt; &amp; co', body)
        self.assertNotIn('<b>Gras</b>', body)

    def test_pdf_de_l_odj_et_du_compte_rendu(self):
        odj, cr = self._odj(), self._cr()
        self._ajouter(odj, 'Pour préparer')
        self._ajouter(cr, 'Pour la suite', bf_visibility_window='post')
        for rec, nom in ((odj, 'Pour préparer'), (cr, 'Pour la suite')):
            with self.subTest(model=rec._name):
                html = self._pdf(rec)
                self.assertRegex(html, r'section-title">\s*Documents\s*<')
                self.assertIn('<a href="%s">%s</a>' % (LIEN, nom), html)

    # -- le renvoi ---------------------------------------------------------

    def _changement_documents(self, odj):
        diff = odj._changes_since_sent()
        return next((e for e in diff['head'] if e['key'] == 'documents'), None)

    def test_un_renvoi_dit_les_documents_ajoutes_et_retires(self):
        odj = self._odj()
        ancien = self._ajouter(odj, 'Version 1')
        odj.action_mark_sent_manually()
        self.assertIsNone(self._changement_documents(odj), "rien n'a bougé")
        self._ajouter(odj, 'Version 2')
        ancien.unlink()
        entree = self._changement_documents(odj)
        self.assertEqual(entree['added'], ['Version 2'])
        self.assertEqual(entree['removed'], ['Version 1'])
        odj.resend_include_changes = True
        bloc = odj.resend_changes_block_html()
        self.assertIn('Documents', bloc)
        self.assertIn('Version 2', bloc)

    def test_un_pre_read_echu_n_est_pas_retire(self):
        """À l'heure de la rencontre, le pre-read n'est plus visible : il ne part
        plus, mais il n'a pas été « retiré » de ce que les destinataires tiennent."""
        odj = self._odj()
        doc = self._ajouter(odj, 'Pre-read', bf_visibility_window='pre')
        odj.action_mark_sent_manually()
        doc.bf_visible_until = _quand(minutes=-5)
        self.assertEqual(self._noms(odj), [], "il ne part plus")
        self.assertIsNone(self._changement_documents(odj))
        self.assertEqual(odj._changes_since_sent()['count'], 0)

    def test_deux_documents_du_meme_nom(self):
        odj = self._odj()
        premier = self._ajouter(odj, 'Annexe', url=LIEN + '/1')
        self._ajouter(odj, 'Annexe', url=LIEN + '/2')
        odj.action_mark_sent_manually()
        premier[:1].unlink()
        self.assertIsNone(self._changement_documents(odj), "pas de ligne vide")

    def test_un_document_hors_fenetre_ne_compte_pas(self):
        odj = self._odj()
        odj.action_mark_sent_manually()
        self._ajouter(odj, 'Pour après', bf_visibility_window='post')
        self.assertIsNone(self._changement_documents(odj))

    def test_un_repere_d_avant_ne_compare_pas_les_documents(self):
        """Repère pris par 18.0.3.71.0 : pas de clé « documents ». Le comparer
        annoncerait comme ajoutés tous les documents déjà partis."""
        odj = self._odj()
        self._ajouter(odj, 'Déjà parti')
        odj.action_mark_sent_manually()
        repere = json.loads(odj.sent_snapshot_json)
        del repere['head']['documents']
        odj.sudo().sent_snapshot_json = json.dumps(repere)
        self.assertIsNone(self._changement_documents(odj))
        self.assertEqual(odj._changes_since_sent()['count'], 0)

    # -- la migration ------------------------------------------------------

    def test_migration_marque_les_seules_pieces_de_l_onglet(self):
        mig = _load_migration()
        odj, cr = self._odj(), self._cr()
        Attachment = self.env['ir.attachment']
        base = {'bf_meeting_document': False}
        lien = Attachment.create(dict(base, name='Lien', type='url', url=LIEN,
                                      res_model='meeting.agenda', res_id=odj.id))
        fichier = Attachment.create(dict(base, name='Fichier', datas=FICHIER,
                                         res_model='meeting.record', res_id=cr.id))
        image = Attachment.create(dict(base, name='image.png', datas=PNG_1PX,
                                       res_model='meeting.agenda', res_id=odj.id))
        image.generate_access_token()
        echange = Attachment.create(dict(
            base, name='compte-rendu-essai-2026-10-25.json', datas=FICHIER,
            mimetype='application/json', res_model='meeting.record', res_id=cr.id))
        envoye = self._cr()
        envoye.report_sent_date = fields.Datetime.now()
        lien_envoye = Attachment.create(dict(base, name='Lien d\'un CR envoyé', type='url', url=LIEN,
                                             res_model='meeting.record', res_id=envoye.id))
        lien_cr = Attachment.create(dict(base, name='Lien d\'un CR à envoyer', type='url', url=LIEN,
                                         res_model='meeting.record', res_id=cr.id))
        ailleurs = Attachment.create(dict(base, name='Autre modèle', type='url', url=LIEN,
                                          res_model='res.partner', res_id=self.alice.id))
        odj.message_post(body='Pièce au chatter', attachments=[('chatter.txt', b'chatter')])
        chatter = Attachment.search([('res_model', '=', 'meeting.agenda'),
                                     ('res_id', '=', odj.id), ('name', '=', 'chatter.txt')])
        self.assertTrue(chatter, "précondition : la pièce du chatter existe")
        chatter.bf_meeting_document = False
        # Un LIEN joint à un message : rare, mais le type seul ne l'écarte pas.
        lien_message = Attachment.create(dict(base, name='Lien joint au message', type='url',
                                              url=LIEN, res_model='meeting.agenda', res_id=odj.id))
        odj.message_post(body='Lien au chatter', attachment_ids=lien_message.ids)
        self.assertTrue(self.env['mail.message'].search_count([('attachment_ids', 'in', lien_message.ids)]),
                        "précondition : le lien est joint au message")
        self.env.flush_all()
        mig.mark_existing_documents(self.env.cr)
        Attachment.invalidate_model(['bf_meeting_document'])
        self.assertTrue(lien.bf_meeting_document)
        self.assertFalse(fichier.bf_meeting_document,
                         "un fichier peut venir du trombone du chatter : il se coche à la main")
        self.assertFalse(image.bf_meeting_document)
        self.assertTrue(lien_cr.bf_meeting_document)
        self.assertFalse(lien_envoye.bf_meeting_document,
                         "le portail le montrerait comme parti avec le compte rendu")
        self.assertFalse(echange.bf_meeting_document)
        self.assertFalse(ailleurs.bf_meeting_document)
        self.assertFalse(chatter.bf_meeting_document)
        self.assertFalse(lien_message.bf_meeting_document)

    def _source(self, xmlid):
        template = self.env.ref(xmlid)
        self.env.flush_all()
        self.env.cr.execute("SELECT body_html ->> 'en_US' FROM mail_template WHERE id = %s",
                            [template.id])
        return template, self.env.cr.fetchone()[0]

    def test_la_source_porte_le_bloc_de_la_migration(self):
        mig = _load_migration()
        for xmlid in mig.TEMPLATES:
            with self.subTest(xmlid=xmlid):
                _template, source = self._source(xmlid)
                self.assertEqual(source.count(mig.BLOCK + mig.ANCHOR), 1)

    def test_migration_laisse_un_gabarit_retouche(self):
        """Paragraphe de clôture recopié à la main : où insérer ? Nulle part."""
        mig = _load_migration()
        double = '<p>Avant</p>' + mig.ANCHOR + 'x</p>' + mig.ANCHOR + 'y</p>'
        self.assertIsNone(mig.insert_block(double))
        self.assertIsNone(mig.insert_block('<p>Sans clôture</p>'))
        deja = mig.BLOCK + mig.ANCHOR + 'z</p>'
        self.assertIsNone(mig.insert_block(deja), "déjà porté : pas une deuxième fois")
        self.assertEqual(mig.insert_block(mig.ANCHOR + 'z</p>'), deja)

    def test_migration_porte_la_section_dans_chaque_traduction(self):
        mig = _load_migration()
        for xmlid in mig.TEMPLATES:
            with self.subTest(xmlid=xmlid):
                template, source = self._source(xmlid)
                ancien = source.replace(mig.BLOCK, '')
                self.assertNotIn('_bf_shared_document_links', ancien)
                self.env.cr.execute(
                    "UPDATE mail_template SET body_html = jsonb_set(jsonb_set(body_html, "
                    "'{fr_CA}', to_jsonb(%s::text)), '{en_CA}', to_jsonb(%s::text)) WHERE id = %s",
                    [ancien, ancien.replace(mig.ANCHOR, ''), template.id])
                template.invalidate_recordset(['body_html'])
                mig.migrate(self.env.cr, '18.0.3.71.0')
                mig.migrate(self.env.cr, '18.0.3.71.0')
                self.env.cr.execute("SELECT body_html FROM mail_template WHERE id = %s",
                                    [template.id])
                corps = self.env.cr.fetchone()[0]
                self.assertEqual(corps['en_US'], source, "la source n'est pas touchée")
                self.assertEqual(corps['fr_CA'], source, "insérée une fois, au bon endroit")
                self.assertNotIn('_bf_shared_document_links', corps['en_CA'],
                                 "sans paragraphe de clôture : laissée telle quelle")

    def test_la_traduction_livree_suit_le_gabarit(self):
        """fr_CA.po porte le corps entier des deux courriels : son texte source
        doit être le gabarit livré, et sa traduction porter la section."""
        from odoo.tools.translate import PoFileReader
        mig = _load_migration()
        po = Path(__file__).resolve().parent.parent / 'i18n' / 'fr_CA.po'
        for xmlid in mig.TEMPLATES:
            with self.subTest(xmlid=xmlid):
                _template, source = self._source(xmlid)
                with po.open('rb') as fh:
                    entrees = [e for e in PoFileReader(fh)
                               if e.get('name') == 'mail.template,body_html'
                               and e.get('imd_name') == xmlid.split('.')[1]]
                self.assertEqual(len(entrees), 1)
                self.assertEqual(entrees[0]['src'], source)
                self.assertEqual(entrees[0]['value'].count(mig.BLOCK + mig.ANCHOR), 1)
