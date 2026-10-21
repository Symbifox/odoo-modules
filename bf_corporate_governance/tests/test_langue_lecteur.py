"""La gouvernance corporative parle la langue de qui la lit.

La source du module est maintenant anglaise ; le français vit dans
`i18n/fr_CA.po`. Le lot a corrigé le code là où un texte échappait au catalogue :

- le titre d'en-tête du PDF d'une résolution (corporate_resolution.py:276-282)
  était écrit en dur en français ;
- la qualité « Administrateur » du repli sur le conseil
  (corporate_resolution.py:238) ;
- la qualité imprimée d'un signataire (corporate_resolution_signatory.py:101) se
  recalcule dans la langue de lecture (`depends_context('lang')`) ;
- l'activité du travail planifié des échéances (corporate_compliance.py:121-145)
  est écrite, et notifiée, dans la langue de CHAQUE gestionnaire assigné ;
- le titre de fenêtre du tableau de bord (corporate_dashboard.js:35) passe par `_t`.

Chaque essai lit le même texte en fr_CA puis en en_CA, ou fait agir une langue
pour un lecteur de l'autre. Les langues sont activées et épinglées : selon la
base, en_US est absente ou seule, et l'administrateur peut être en français.
"""

import glob
import os
from datetime import timedelta

from babel.messages import extract

from odoo import Command, fields
from odoo.modules.module import get_module_path
from odoo.tests import HttpCase, TransactionCase, new_test_user, tagged

GESTION = 'base.group_user,bf_corporate_governance.group_corporate_manager'


def _langues(env):
    for code in ('fr_CA', 'en_CA'):
        env['res.lang']._activate_lang(code)
    env['ir.module.module']._load_module_terms(
        ['bf_corporate_governance'], ['fr_CA'], overwrite=True)


@tagged('post_install', '-at_install')
class TestResolution(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _langues(cls.env)
        # Société dédiée : le repli lit TOUT le registre des administrateurs de la société.
        cls.societe = cls.env['res.company'].create({'name': 'Société langue'})
        cls.env = cls.env(context=dict(cls.env.context, allowed_company_ids=[cls.societe.id]))
        cls.administrateur = cls.env['res.partner'].create({'name': 'Administratrice Langue'})
        cls.env['corporate.director'].create({
            'partner_id': cls.administrateur.id,
            'appointment_date': fields.Date.today() - timedelta(days=30),
            'company_id': cls.societe.id,
        })

    def _resolution(self, resolution_type):
        return self.env['corporate.resolution'].create({
            'name': 'Résolution langue', 'resolution_type': resolution_type,
            'meeting_date': fields.Date.today(), 'company_id': self.societe.id,
        })

    def test_titre_d_en_tete_du_pdf(self):
        """corporate_resolution.py:276-282 : les trois titres suivent la langue d'impression."""
        attendus = {
            'written_board': ("Résolution écrite des administrateurs",
                              "Written directors' resolution"),
            'members': ("Résolution de l'assemblée des membres", "Members' meeting resolution"),
            'written_shareholder': ("Résolution écrite des actionnaires",
                                    "Written shareholder resolution"),
        }
        for type_, (fr, en) in attendus.items():
            with self.subTest(type=type_):
                resolution = self._resolution(type_)
                self.assertEqual(resolution.with_context(lang='fr_CA')._get_resolution_type_label(), fr)
                self.assertEqual(resolution.with_context(lang='en_CA')._get_resolution_type_label(), en)

    def test_titre_imprime_dans_le_pdf_anglais(self):
        """Le gabarit imprime le titre dans la langue du rendu."""
        resolution = self._resolution('written_board')
        rendu = str(self.env['ir.qweb'].with_context(lang='en_CA')._render(
            'bf_corporate_governance.report_corporate_resolution',
            {'docs': resolution.with_context(lang='en_CA'), 'env': self.env(context=dict(
                self.env.context, lang='en_CA'))},
        )).replace('&#39;', "'")
        self.assertIn("Written directors' resolution", rendu)
        self.assertNotIn("Résolution écrite des administrateurs", rendu)

    def test_qualite_du_repli_sur_le_conseil(self):
        """corporate_resolution.py:238 : « Administrateur » / « Director » sous chaque nom."""
        resolution = self._resolution('written_board')
        fr = resolution.with_context(lang='fr_CA')._get_signatories()
        en = resolution.with_context(lang='en_CA')._get_signatories()
        self.assertEqual([(s['name'], s['capacity']) for s in fr],
                         [('Administratrice Langue', 'Administrateur')])
        self.assertEqual([(s['name'], s['capacity']) for s in en],
                         [('Administratrice Langue', 'Director')])

    def test_qualite_imprimee_relue_dans_chaque_langue(self):
        """corporate_resolution_signatory.py:101 : le libellé non stocké ne garde pas la
        première langue lue."""
        resolution = self._resolution('members')
        signataire = self.env['res.partner'].create({'name': 'Secrétaire Langue'})
        resolution.signatory_ids = [Command.create({
            'partner_id': signataire.id, 'capacity': 'assembly_secretary'})]
        ligne = resolution.signatory_ids
        ligne.invalidate_recordset()
        self.assertEqual(ligne.with_context(lang='en_CA').capacity_label, "Meeting secretary")
        self.assertEqual(ligne.with_context(lang='fr_CA').capacity_label, "Secrétariat d'assemblée")
        self.assertEqual(ligne.with_context(lang='en_CA').capacity_label, "Meeting secretary")


@tagged('post_install', '-at_install')
class TestConformite(TransactionCase):
    """corporate_compliance.py:121-145 : une activité par gestionnaire, dans SA langue."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _langues(cls.env)
        cls.gestion_fr = new_test_user(cls.env, login='l9.gouv.fr', groups=GESTION, lang='fr_CA')
        cls.gestion_en = new_test_user(cls.env, login='l9.gouv.en', groups=GESTION, lang='en_CA')

    def _echeance(self, jours):
        return self.env['corporate.compliance.event'].create({
            'name': 'Déclaration langue', 'event_type': 'annual_declaration',
            'due_date': fields.Date.today() + timedelta(days=jours),
        })

    def _activite(self, echeance, usager):
        activite = self.env['mail.activity'].search([
            ('res_model', '=', 'corporate.compliance.event'), ('res_id', '=', echeance.id),
            ('user_id', '=', usager.id),
        ])
        self.assertEqual(len(activite), 1)
        return str(activite.note)

    def _objet_de_l_avis(self, echeance, usager):
        # Seul l'avis d'activité est attendu ici (l'échéance n'a pas d'assigné propre) ;
        # filtrer sur l'objet écarte tout autre avis de l'usager.
        avis = self.env['mail.message'].search([
            ('subject', '!=', False),
            ('model', '=', 'corporate.compliance.event'), ('res_id', '=', echeance.id),
            ('message_type', '=', 'user_notification'),
            ('partner_ids', 'in', usager.partner_id.ids),
        ])
        self.assertEqual(len(avis), 1)
        return avis.subject or ''

    def _cron(self, lang):
        self.env['corporate.compliance.event'].with_context(
            lang=lang, mail_notify_force_send=False)._cron_check_compliance_deadlines()

    def test_note_en_retard_dans_la_langue_de_chaque_gestionnaire(self):
        """« EN RETARD » / « OVERDUE », et la note entière, selon l'assigné."""
        for lang_du_travail in ('fr_CA', 'en_CA'):
            with self.subTest(travail=lang_du_travail):
                echeance = self._echeance(-2)
                self._cron(lang_du_travail)
                fr = self._activite(echeance, self.gestion_fr)
                en = self._activite(echeance, self.gestion_en)
                self.assertIn('<strong>Conformité corporative (EN RETARD)</strong>', fr)
                self.assertIn('Échéance :', fr)
                self.assertIn('<strong>Corporate compliance (OVERDUE)</strong>', en)
                self.assertIn('Due date:', en)
                self.assertNotIn('Conformité', en)

    def test_note_a_venir_dans_la_langue_de_chaque_gestionnaire(self):
        """« 10 jours » / « 10 days »."""
        echeance = self._echeance(10)
        self._cron('en_CA')
        self.assertIn('(10 jours)', self._activite(echeance, self.gestion_fr))
        self.assertIn('(10 days)', self._activite(echeance, self.gestion_en))

    def test_objet_de_l_avis_dans_la_langue_de_l_assigne(self):
        """L'activité est créée dans l'environnement de l'assigné : l'objet de l'avis
        d'Odoo (`… assigned to you`, traduit par `_()` dans la langue de l'appelant)
        suit donc l'assigné, et non le travail planifié."""
        echeance = self._echeance(-2)
        self._cron('en_CA')
        self.assertIn('vous est attribuée', self._objet_de_l_avis(echeance, self.gestion_fr))
        echeance = self._echeance(-3)
        self._cron('fr_CA')
        objet = self._objet_de_l_avis(echeance, self.gestion_en)
        self.assertIn('assigned to you', objet)
        self.assertNotIn('vous est attribuée', objet)


@tagged('post_install', '-at_install')
class TestClientWeb(HttpCase):
    """corporate_dashboard.js:35 : le titre de fenêtre passe par `_t`."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _langues(cls.env)

    def test_titre_extrait_et_servi_en_francais(self):
        racine = get_module_path('bf_corporate_governance')
        extraits = set()
        for chemin in sorted(glob.glob(os.path.join(racine, 'static/src/js/*.js'))):
            with open(chemin, 'rb') as f:
                extraits.update(m[1] for m in extract.extract('javascript', f, keywords={'_t': None}))
        self.assertIn('Overdue compliance', extraits)
        rep = self.url_open('/web/webclient/translations/essai?lang=fr_CA', timeout=60)
        self.assertEqual(rep.status_code, 200, rep.text[:300])
        propres = {m['id']: m['string'] for m in
                   rep.json()['modules'].get('bf_corporate_governance', {}).get('messages', [])}
        self.assertEqual(propres.get('Overdue compliance'), 'Conformité en retard')
