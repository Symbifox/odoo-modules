"""Les textes du coffre d'identifiants dans la langue de qui les lit.

La source du module est maintenant anglaise ; le français vit dans
`i18n/fr_CA.po`. Le lot a aussi corrigé le code là où un texte échappait au
catalogue :

- les raisons du garde-fou des graines (otp_secret_guard.py:99-113) étaient des
  chaînes françaises hors catalogue, insérées telles quelles dans le message
  d'erreur : un usager anglais lisait « une adresse otpauth:// » ;
- les notes du travail planifié d'expiration (project_credential.py:832-850)
  suivent la langue de qui suit l'identifiant (chef de projet, sinon créateur),
  et non celle du travail ;
- le titre de la fenêtre « Identifiants - projet » (project_project.py:40) ;
- la note de rotation (credential_rotate_wizard.py:83-88) : libellé de la raison
  traduit (et non la source de `selection`), corps en Markup ;
- les titres de fenêtre du tableau de bord et le texte de repli du champ copiable
  (static/src/js) passent par `_t`.

Chaque essai lit le même texte en fr_CA puis en en_CA : un texte resté français,
ou anglais, pour tous le fait tomber. Les langues sont activées et épinglées sur
chaque usager : selon la base, en_US est absente ou seule, et l'administrateur
peut être réglé en français.
"""

import glob
import os
from datetime import timedelta
from unittest.mock import patch

from babel.messages import extract
from cryptography.fernet import Fernet

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.modules.module import get_module_path
from odoo.tests import HttpCase, TransactionCase, new_test_user, tagged

GERANT = 'base.group_user,project.group_project_manager,bf_credentials.group_credential_manager'


def _langues(env):
    for code in ('fr_CA', 'en_CA'):
        env['res.lang']._activate_lang(code)
    env['ir.module.module']._load_module_terms(['bf_credentials'], ['fr_CA'], overwrite=True)


@tagged('post_install', '-at_install')
class TestLangue(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _langues(cls.env)
        cls.Credential = cls.env['project.credential']
        cls.type_id = cls.env['project.credential.type'].create(
            {'name': 'Type langue', 'code': 'TEST-LANGUE-L9'})
        cls.chef_fr = new_test_user(cls.env, login='l9.chef.fr', groups=GERANT, lang='fr_CA')
        cls.chef_en = new_test_user(cls.env, login='l9.chef.en', groups=GERANT, lang='en_CA')
        cls.projet_fr = cls.env['project.project'].create(
            {'name': 'Projet langue FR', 'user_id': cls.chef_fr.id})
        cls.projet_en = cls.env['project.project'].create(
            {'name': 'Projet langue EN', 'user_id': cls.chef_en.id})
        cls.projet_sans_chef = cls.env['project.project'].create(
            {'name': 'Projet sans chef', 'user_id': False})

    def _identifiant(self, projet, **valeurs):
        return self.Credential.create(dict({
            'name': 'Identifiant langue', 'project_id': projet.id,
            'type_id': self.type_id.id,
        }, **valeurs))

    # ── Garde-fou des graines (otp_secret_guard.py:99,102,113) ─────────
    def _refus(self, lang, **valeurs):
        with self.assertRaises(ValidationError) as cm:
            self.Credential.with_context(lang=lang).create(dict({
                'name': 'Identifiant graine', 'project_id': self.projet_fr.id,
                'type_id': self.type_id.id, 'mfa_type': 'totp',
            }, **valeurs))
        return str(cm.exception)

    def test_garde_adresse_otpauth(self):
        """otp_secret_guard.py:99 : la raison « adresse otpauth:// » est traduite."""
        valeur = 'otpauth://totp/Exemple:compte?secret=JBSWY3DPEHPK3PXP&issuer=Exemple'
        fr = self._refus('fr_CA', mfa_reference=valeur)
        self.assertIn("Le champ « Référence chez le porteur » a reçu "
                      "une adresse otpauth://, qui porte la graine elle-même.", fr)
        en = self._refus('en_CA', mfa_reference=valeur)
        self.assertIn('The "Reference at the holder" field received '
                      'an otpauth:// address, which carries the seed itself.', en)
        self.assertNotIn('graine', en)

    def test_garde_parametre_secret(self):
        """otp_secret_guard.py:102 : la raison « paramètre secret= » est traduite."""
        valeur = 'compte secret=JBSWY3DPEHPK3PXP'
        fr = self._refus('fr_CA', notes=valeur)
        self.assertIn("un paramètre secret= suivi d'une graine en base32", fr)
        en = self._refus('en_CA', notes=valeur)
        self.assertIn('a secret= parameter followed by a base32 seed', en)
        self.assertNotIn('graine', en)

    def test_garde_graine_base32(self):
        """otp_secret_guard.py:113 : la raison « graine en base32 » est traduite."""
        fr = self._refus('fr_CA', mfa_reference='JBSWY3DPEHPK3PXP')
        self.assertIn('a reçu une graine en base32.', fr)
        en = self._refus('en_CA', mfa_reference='JBSWY3DPEHPK3PXP')
        self.assertIn('field received a base32 seed.', en)
        self.assertNotIn('graine', en)

    # ── Travail planifié d'expiration (project_credential.py:832,847) ──
    def _notes(self, identifiant):
        return ' '.join(str(m.body) for m in self.env['mail.message'].search([
            ('model', '=', 'project.credential'), ('res_id', '=', identifiant.id),
        ]))

    def test_expire_dans_la_langue_du_chef_de_projet(self):
        """project_credential.py:832 : « Identifiant expiré. » suit le chef du projet.

        Chaque identifiant passe seul au travail lancé dans l'AUTRE langue (un
        identifiant expiré n'est plus repris au passage suivant)."""
        hier = fields.Date.today() - timedelta(days=1)
        cred_fr = self._identifiant(self.projet_fr, expiration_date=hier)
        self.Credential.with_context(lang='en_CA')._cron_check_expiring_credentials()
        self.assertIn('Identifiant expiré.', self._notes(cred_fr))
        self.assertNotIn('Credential expired.', self._notes(cred_fr))
        cred_en = self._identifiant(self.projet_en, expiration_date=hier)
        self.Credential.with_context(lang='fr_CA')._cron_check_expiring_credentials()
        self.assertIn('Credential expired.', self._notes(cred_en))
        self.assertNotIn('Identifiant expiré.', self._notes(cred_en))

    def test_expire_bientot_dans_la_langue_du_chef_de_projet(self):
        """project_credential.py:847 : « L'identifiant expirera le … » suit le chef du projet."""
        dans_dix = fields.Date.today() + timedelta(days=10)
        cred_fr = self._identifiant(self.projet_fr, expiration_date=dans_dix)
        self.Credential.with_context(lang='en_CA')._cron_check_expiring_credentials()
        self.assertIn("L'identifiant expirera le", self._notes(cred_fr).replace('&#39;', "'"))
        self.assertNotIn('The credential will expire on', self._notes(cred_fr))
        cred_en = self._identifiant(self.projet_en, expiration_date=dans_dix)
        self.Credential.with_context(lang='fr_CA')._cron_check_expiring_credentials()
        self.assertIn('The credential will expire on', self._notes(cred_en))
        self.assertNotIn('expirera', self._notes(cred_en))

    def test_expire_sans_chef_dans_la_langue_du_createur(self):
        """project_credential.py:792 (`_langue_du_responsable`) : sans chef, le créateur."""
        self.projet_sans_chef.message_subscribe(partner_ids=self.chef_en.partner_id.ids)
        hier = fields.Date.today() - timedelta(days=1)
        cred = self.Credential.with_user(self.chef_en).create({
            'name': 'Identifiant créé en anglais', 'project_id': self.projet_sans_chef.id,
            'type_id': self.type_id.id, 'expiration_date': hier,
        })
        self.assertEqual(cred.create_uid, self.chef_en)
        self.Credential.with_context(lang='fr_CA')._cron_check_expiring_credentials()
        self.assertIn('Credential expired.', self._notes(cred))
        self.assertNotIn('Identifiant expiré.', self._notes(cred))

    # ── Titre de la fenêtre du projet (project_project.py:40) ──────────
    def test_titre_des_identifiants_du_projet(self):
        self.assertEqual(
            self.projet_fr.with_context(lang='fr_CA').action_view_credentials()['name'],
            'Identifiants - Projet langue FR')
        self.assertEqual(
            self.projet_fr.with_context(lang='en_CA').action_view_credentials()['name'],
            'Credentials - Projet langue FR')

    # ── Note de rotation (credential_rotate_wizard.py:83-88) ───────────
    def _rotation(self, lang, cred):
        sorcier = self.env['project.credential.rotate.wizard'].with_context(lang=lang).create({
            'credential_id': cred.id, 'new_password': 'nouveau-42', 'confirm_password': 'nouveau-42',
            'rotation_reason': 'scheduled', 'notes': '<i>note</i>',
        })
        # Une clé de chiffrement le temps de l'essai : la base d'essai n'en porte pas, et la
        # rotation écrit le nouveau mot de passe chiffré avant de poser sa note.
        cle = {self.Credential._CLE_ENV: Fernet.generate_key().decode()}
        with patch.dict(os.environ, cle):
            sorcier.action_rotate()
        return self._notes(cred)

    def test_note_de_rotation_en_francais(self):
        """Raison traduite (pas la source de `selection`), balises rendues, notes échappées."""
        corps = self._rotation('fr_CA', self._identifiant(self.projet_fr))
        self.assertIn('Mot de passe changé.', corps)
        self.assertIn('<b>Raison :</b> Rotation planifiée', corps)
        self.assertIn('<b>Notes :</b> &lt;i&gt;note&lt;/i&gt;', corps)
        self.assertNotIn('Scheduled rotation', corps)

    def test_note_de_rotation_en_anglais(self):
        corps = self._rotation('en_CA', self._identifiant(self.projet_en))
        self.assertIn('Password changed.', corps)
        self.assertIn('<b>Reason:</b> Scheduled rotation', corps)
        self.assertIn('<b>Notes:</b> &lt;i&gt;note&lt;/i&gt;', corps)
        self.assertNotIn('Rotation planifiée', corps)


# Textes du client web que le lot fait passer par `_t`, avec leur français.
ATTENDU_JS = {
    # credential_dashboard.js:30,45,56,71,86,101 (titres de fenêtre)
    "Credentials": "Identifiants",
    "Credentials expiring within 30 days": "Identifiants expirant sous 30 jours",
    "Expired credentials": "Identifiants expirés",
    "Second factor to document": "Deuxième facteur à documenter",
    "Second factor without fallback": "Deuxième facteur sans relève",
    "Revoked credentials": "Identifiants révoqués",
    # credential_copy.js:27 (texte de repli du champ, autrefois littéral du gabarit)
    "Enter password...": "Saisir le mot de passe...",
}


@tagged('post_install', '-at_install')
class TestClientWeb(HttpCase):
    """static/src/js : les titres passent par `_t` et le client les reçoit en français."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _langues(cls.env)

    def test_textes_extraits_par_odoo(self):
        """Mêmes extracteur et mot-clé que l'export des termes : sans `_t`, un titre
        n'atteint jamais le catalogue, donc jamais l'usager."""
        racine = get_module_path('bf_credentials')
        extraits = set()
        for chemin in sorted(glob.glob(os.path.join(racine, 'static/src/js/*.js'))):
            with open(chemin, 'rb') as f:
                extraits.update(m[1] for m in extract.extract('javascript', f, keywords={'_t': None}))
        manquants = sorted(set(ATTENDU_JS) - extraits)
        self.assertFalse(manquants, "Hors `_t` dans static/src/js : %r" % manquants)

    def test_client_recoit_le_francais(self):
        rep = self.url_open('/web/webclient/translations/essai?lang=fr_CA', timeout=60)
        self.assertEqual(rep.status_code, 200, rep.text[:300])
        propres = {m['id']: m['string'] for m in
                   rep.json()['modules'].get('bf_credentials', {}).get('messages', [])}
        autres = {k: (v, propres.get(k)) for k, v in ATTENDU_JS.items() if propres.get(k) != v}
        self.assertFalse(autres, "Absents ou autres dans le bloc du module : %r" % autres)
