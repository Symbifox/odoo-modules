# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Langue des textes écrits en base par le module d'hébergement (partie B).

Le module passe d'une source française à une source anglaise ; le français vit dans
`i18n/fr_CA.po`. Chaque texte écrit en base l'est dans la langue de QUI LE LIRA :

- alertes ntfy, notes au fil des opérations, journal d'audit, noms stockés : l'équipe
  d'hébergement, donc la langue de la société (`hosting_lang.reader_env`), même quand un
  usager d'une autre langue, un travail planifié ou une route publique les déclenche ;
- activités : la langue de l'assigné, et l'activité est créée dans son env (l'objet de
  l'avis « … vous a été assigné » se traduit sur l'env de l'activité) ;
- courriel d'expiration des licences : la langue du destinataire ;
- exceptions et notifications montrées à l'usager : la langue de l'usager.

Chaque essai met la langue du LECTEUR à l'opposé de celle du déclencheur : un correctif
qui reprendrait la langue du déclencheur, ou le texte français d'origine écrit en dur,
fait tomber l'assertion.

La migration 18.0.2.63.0 est éprouvée sur des données préparées : texte LIVRÉ français
basculé en anglais pour en_US (français gardé en fr_CA), retouche laissée, et champ
traduit terme à terme (notes des logiciels) qui garde son français.

Les essais épinglent fr_CA et en_CA, qu'ils activent ; rien ne sort (ntfy et envoi des
courriels remplacés).
"""

import importlib.util
import json
import os
from contextlib import contextmanager
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import requests
from psycopg2.extras import Json

from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.modules.module import get_module_path
from odoo.tests import TransactionCase, new_test_user, tagged
from odoo.tools import SQL

from ..controllers import backup_api, provisioning_api
from ..models import voipms_connector

MODULE = "hosting_management"


def _migration(nom):
    """Charge un script de migration 18.0.2.63.0 (nom à tiret, hors paquet)."""
    chemin = os.path.join(get_module_path(MODULE), "migrations", "18.0.2.63.0", nom)
    spec = importlib.util.spec_from_file_location(
        "hosting_l9b_" + nom.replace("-", "_").removesuffix(".py"), chemin)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@tagged("post_install", "-at_install")
class TestLangueB(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for code in ("fr_CA", "en_CA"):
            cls.env["res.lang"]._activate_lang(code)
        cls.env["ir.module.module"]._load_module_terms([MODULE], ["fr_CA"], overwrite=True)
        cls.user_fr = new_test_user(
            cls.env, login="l9b_fr", name="Francine EssaiB", lang="fr_CA",
            email="francine.l9b@exemple.test", notification_type="inbox",
            groups="base.group_user")
        cls.user_en = new_test_user(
            cls.env, login="l9b_en", name="Edward EssaiB", lang="en_CA",
            email="edward.l9b@exemple.test", notification_type="inbox",
            groups="base.group_user")
        cls.client = cls.env["res.partner"].create(
            {"name": "Client EssaiB", "is_company": True, "lang": "en_CA"})
        cls.logiciel = cls.env["hosting.software"].create({
            "name": "Logiciel EssaiB", "code": "L9B", "software_type": "self_hosted",
        })
        cls.service = cls.env["hosting.service"].create({
            "name": "Service EssaiB", "partner_id": cls.client.id,
            "software_id": cls.logiciel.id,
        })

    def setUp(self):
        super().setUp()
        # Rien ne sort : l'envoi réel des courriels est remplacé (le mail.mail reste lisible).
        patcher = patch("odoo.addons.mail.models.mail_mail.MailMail.send",
                        lambda s, *a, **k: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    # ------------------------------------------------------------------ outils
    def _societe(self, lang):
        """La langue du lecteur « équipe d'hébergement » : celle de la société."""
        self.env.company.partner_id.lang = lang

    def _env(self, lang):
        """L'env du déclencheur (usager, travail planifié ou route), épinglé à `lang`."""
        return self.env(context=dict(self.env.context, lang=lang))

    @contextmanager
    def _ntfy(self):
        envois = []

        def faux_send(self_, title, body, priority="default", tags=None, click=None,
                      topic=None):
            envois.append({"title": title, "body": body})
            return True

        with patch.object(type(self.env["hosting.ntfy"]), "send", faux_send):
            yield envois

    def _corps(self, rec):
        """Corps des messages au fil d'un enregistrement (le plus récent d'abord)."""
        msgs = self.env["mail.message"].search(
            [("model", "=", rec._name), ("res_id", "=", rec.id)], order="id desc")
        return [str(m.body or "") for m in msgs]

    def _note_contient(self, rec, attendu):
        corps = self._corps(rec)
        self.assertTrue(any(attendu in c for c in corps),
                        f"{attendu!r} absent du fil : {corps}")
        return next(c for c in corps if attendu in c)

    def _sujets_avis(self, rec, resume):
        """Objets des avis d'activité assignée (message_notify) qui citent le résumé
        `resume` (l'avis « You have been assigned to … » du champ assigné est écarté)."""
        sujets = self.env["mail.message"].search([
            ("model", "=", rec._name), ("res_id", "=", rec.id),
            ("message_type", "=", "user_notification"),
        ]).mapped("subject")
        avis = [s for s in sujets if resume in (s or "")]
        self.assertTrue(avis, f"aucun avis d'activité citant {resume!r} : {sujets}")
        return avis

    def _audit(self, res_model, res_id):
        return self.env["hosting.audit.log"].search(
            [("res_model", "=", res_model), ("res_id", "=", res_id)], order="id desc")

    # ================================================================ VOIP.ms
    def test_voipms_methode_refusee_dans_la_langue_de_l_usager(self):
        """voipms_connector.py:75 : l'exception suit la langue de l'usager."""
        Did = self.env["hosting.voip.did"]
        with self.assertRaises(ValueError) as en:
            Did.with_env(self._env("en_CA"))._voipms_api_call("setDIDInfo")
        self.assertEqual(str(en.exception),
                         "VOIP.ms method not allowed (read-only integration): setDIDInfo")
        with self.assertRaises(ValueError) as fr:
            Did.with_env(self._env("fr_CA"))._voipms_api_call("setDIDInfo")
        self.assertEqual(str(fr.exception),
                         "Méthode VOIP.ms refusée (intégration en lecture seule) : setDIDInfo")

    def test_voipms_identifiants_absents_dans_la_langue_de_l_usager(self):
        """voipms_connector.py:80."""
        ICP = self.env["ir.config_parameter"].sudo()
        ICP.set_param("hosting.voipms_api_username", "")
        ICP.set_param("hosting.voipms_api_password", "")
        Did = self.env["hosting.voip.did"]
        with self.assertRaises(ValueError) as en:
            Did.with_env(self._env("en_CA"))._voipms_api_call("getBalance")
        self.assertIn("VOIP.ms API credentials missing", str(en.exception))
        with self.assertRaises(ValueError) as fr:
            Did.with_env(self._env("fr_CA"))._voipms_api_call("getBalance")
        self.assertIn("Identifiants API VOIP.ms absents", str(fr.exception))

    def test_voipms_echec_reseau_dans_la_langue_de_l_usager_sans_secret(self):
        """voipms_connector.py:99 : message traduit, mot de passe toujours caviardé."""
        ICP = self.env["ir.config_parameter"].sudo()
        ICP.set_param("hosting.voipms_api_username", "essai@exemple.test")
        ICP.set_param("hosting.voipms_api_password", "secret-l9b")

        def panne(*a, **k):
            raise requests.ConnectionError("https://voip.ms/?api_password=secret-l9b down")

        Did = self.env["hosting.voip.did"]
        with patch.object(voipms_connector.requests, "get", panne), \
                patch.object(voipms_connector.time, "sleep", lambda s: None):
            with self.assertRaises(RuntimeError) as en:
                Did.with_env(self._env("en_CA"))._voipms_api_call("getBalance")
            with self.assertRaises(RuntimeError) as fr:
                Did.with_env(self._env("fr_CA"))._voipms_api_call("getBalance")
        self.assertTrue(str(en.exception).startswith("VOIP.ms getBalance failed: ConnectionError: "),
                        str(en.exception))
        self.assertTrue(str(fr.exception).startswith("VOIP.ms getBalance échec : ConnectionError : "),
                        str(fr.exception))
        for exc in (en.exception, fr.exception):
            self.assertNotIn("secret-l9b", str(exc))

    def test_voipms_statut_inattendu_dans_la_langue_de_l_usager(self):
        """voipms_connector.py:118 (getDIDsInfo) et :125 (getBalance)."""
        Did = type(self.env["hosting.voip.did"])
        with patch.object(Did, "_voipms_api_call", lambda self_, m, **p: {"status": "x_l9b"}):
            with self.assertRaises(RuntimeError) as dids_en:
                self.env["hosting.voip.did"].with_env(self._env("en_CA"))._voipms_fetch_dids()
            with self.assertRaises(RuntimeError) as dids_fr:
                self.env["hosting.voip.did"].with_env(self._env("fr_CA"))._voipms_fetch_dids()
            with self.assertRaises(RuntimeError) as bal_en:
                self.env["hosting.voip.did"].with_env(self._env("en_CA"))._voipms_fetch_balance()
            with self.assertRaises(RuntimeError) as bal_fr:
                self.env["hosting.voip.did"].with_env(self._env("fr_CA"))._voipms_fetch_balance()
        self.assertEqual(str(dids_en.exception), "getDIDsInfo: status x_l9b")
        self.assertEqual(str(dids_fr.exception), "getDIDsInfo : statut x_l9b")
        self.assertEqual(str(bal_en.exception), "getBalance: status x_l9b")
        self.assertEqual(str(bal_fr.exception), "getBalance : statut x_l9b")

    def _alerte_solde(self, lang_declencheur):
        ICP = self.env["ir.config_parameter"].sudo()
        ICP.set_param("hosting.voipms_enabled", "1")
        ICP.set_param("hosting.voipms_low_balance_threshold", "10")
        Did = type(self.env["hosting.voip.did"])
        with self._ntfy() as envois, \
                patch.object(Did, "_voipms_fetch_balance", lambda self_: 3.5):
            self.env["hosting.voip.did"].with_env(
                self._env(lang_declencheur))._cron_voipms_balance_alert()
        self.assertEqual(len(envois), 1, envois)
        return envois[0]

    def test_voip_solde_bas_dans_la_langue_de_la_societe(self):
        """hosting_voip_did.py:253 : alerte ntfy du travail planifié, langue de la société."""
        self._societe("en_CA")
        alerte = self._alerte_solde("fr_CA")
        self.assertEqual(alerte["title"], "VOIP.ms — low balance")
        self.assertEqual(alerte["body"],
                         "VOIP.ms balance: $3.50 (threshold $10.00). Top up the account.")
        self._societe("fr_CA")
        alerte = self._alerte_solde("en_CA")
        self.assertEqual(alerte["title"], "VOIP.ms — solde bas")
        self.assertEqual(alerte["body"],
                         "Solde VOIP.ms : 3.50 $ (seuil 10.00 $). Recharger le compte.")

    # ============================================================ Sauvegardes
    def _execution(self, statut="success", lang="fr_CA"):
        return self.env["hosting.backup.run"].with_env(self._env(lang)).create({
            "hostname": "hote-l9b", "report_type": "restic",
            "line_ids": [Command.create({"service_name": "svc-l9b", "status": statut,
                                         "error_message": "disque plein"})],
        })

    def test_sauvegarde_ntfy_echec_dans_la_langue_de_la_societe(self):
        """hosting_backup_log.py:236 : alerte ntfy d'une exécution en échec."""
        self._societe("en_CA")
        run = self._execution("failed", lang="fr_CA")
        self.assertEqual(run.state, "failed")
        with self._ntfy() as envois:
            run._send_backup_ntfy_alert()
        self.assertEqual(envois[0]["title"], f"BACKUP FAILED: {run.name}")
        self.assertIn("Host: hote-l9b", envois[0]["body"])
        self.assertIn("Failed services:", envois[0]["body"])
        self._societe("fr_CA")
        run_en = self._execution("failed", lang="en_CA")
        with self._ntfy() as envois:
            run_en._send_backup_ntfy_alert()
        self.assertEqual(envois[0]["title"], f"SAUVEGARDE ÉCHEC : {run_en.name}")
        self.assertIn("Hôte : hote-l9b", envois[0]["body"])
        self.assertIn("Services en échec :", envois[0]["body"])

    def _reglages_rapport(self, seulement_si_probleme):
        ICP = self.env["ir.config_parameter"].sudo()
        ICP.set_param("hosting.backup_report_enabled", "1")
        ICP.set_param("hosting.backup_report_only_on_issues", "1" if seulement_si_probleme else "0")
        ICP.set_param("hosting.backup_report_recipients", "ops-l9b@exemple.test")

    def test_sauvegarde_note_courriel_non_envoye_dans_la_langue_de_la_societe(self):
        """hosting_backup_log.py:311 : note au fil « courriel non envoyé »."""
        self._reglages_rapport(seulement_si_probleme=True)
        self._societe("en_CA")
        run = self._execution("success", lang="fr_CA")
        self.assertEqual(run.state, "success")
        run.with_env(self._env("fr_CA")).action_send_report()
        self._note_contient(run, "Email not sent (setting: only when there is a problem).")
        self._societe("fr_CA")
        run_fr = self._execution("success", lang="en_CA")
        run_fr.with_env(self._env("en_CA")).action_send_report()
        self._note_contient(
            run_fr, "Courriel non envoyé (configuration : seulement en cas de problème).")

    def test_sauvegarde_note_rapport_envoye_en_markup_et_langue_de_la_societe(self):
        """hosting_backup_log.py:333 : note au fil en Markup (balise non échappée), langue
        de la société."""
        self._reglages_rapport(seulement_si_probleme=False)
        self._societe("fr_CA")
        run = self._execution("success", lang="en_CA")
        run.with_env(self._env("en_CA")).action_send_report()
        corps = self._note_contient(run, "Rapport de sauvegarde envoyé à")
        self.assertIn("<strong>ops-l9b@exemple.test</strong>", corps)
        self.assertNotIn("&lt;strong&gt;", corps)
        self._societe("en_CA")
        run_en = self._execution("success", lang="fr_CA")
        run_en.with_env(self._env("fr_CA")).action_send_report()
        corps = self._note_contient(run_en, "Backup report sent to")
        self.assertIn("<strong>ops-l9b@exemple.test</strong>", corps)

    def test_sauvegarde_courriel_rendu_dans_la_langue_de_la_societe(self):
        """hosting_backup_log.py:328 : gabarit sans `lang` rendu dans la langue de la
        société, pas dans celle du déclencheur."""
        self._reglages_rapport(seulement_si_probleme=False)
        self._societe("fr_CA")
        run = self._execution("success", lang="en_CA")
        run.with_env(self._env("en_CA")).action_send_report()
        courriel = self.env["mail.mail"].search(
            [("model", "=", run._name), ("res_id", "=", run.id)], limit=1)
        self.assertTrue(courriel, "aucun courriel créé")
        self.assertIn("Rapport de sauvegarde", courriel.subject)
        # en_US et non en_CA : la base de BF porte des clés en_CA au français LIVRÉ pour ce
        # gabarit (voir le rapport), qui masqueraient la preuve.
        self._societe("en_US")
        run_en = self._execution("success", lang="fr_CA")
        run_en.with_env(self._env("fr_CA")).action_send_report()
        courriel = self.env["mail.mail"].search(
            [("model", "=", run_en._name), ("res_id", "=", run_en.id)], limit=1)
        self.assertIn("Backup report", courriel.subject)
        self.assertNotIn("Rapport de sauvegarde", courriel.subject)

    def test_sauvegarde_ntfy_echec_d_envoi_du_rapport_dans_la_langue_de_la_societe(self):
        """hosting_backup_log.py:389 : alerte ntfy du travail planifié quand l'envoi échoue."""
        ICP = self.env["ir.config_parameter"].sudo()
        ICP.set_param("hosting.backup_report_enabled", "1")
        ICP.set_param("hosting.backup_report_mode", "scheduled")
        ICP.set_param("hosting.backup_report_send_hour", "0")
        self._societe("en_CA")
        run = self._execution("success", lang="fr_CA")
        Run = type(self.env["hosting.backup.run"])

        def panne(self_):
            raise RuntimeError("smtp l9b")

        with self._ntfy() as envois, patch.object(Run, "action_send_report", panne), \
                patch.object(self.env.cr, "rollback", lambda: None):
            self.env["hosting.backup.run"].with_env(self._env("fr_CA"))._cron_send_daily_report()
        miens = [e for e in envois if run.name in e["title"]]
        self.assertEqual(len(miens), 1, envois)
        self.assertEqual(miens[0]["title"], f"BACKUP REPORT: sending failed ({run.name})")
        self.assertIn("Host: hote-l9b", miens[0]["body"])

    # ============================================================== Domaines
    def _domaine(self, nom, **vals):
        return self.env["hosting.domain"].create(
            {"name": nom, "partner_id": self.client.id, **vals})

    def test_dmarc_non_suivi_note_en_markup_dans_la_langue_de_la_societe(self):
        """hosting_domain.py:392 : note au fil, langue de la société, balise non échappée."""
        self._societe("en_CA")
        dom = self._domaine("nonsuivi-l9b.test")
        dom.with_env(self._env("fr_CA")).action_dmarc_check_now()
        corps = self._note_contient(
            dom, "DMARC: monitoring disabled for this domain, check skipped.")
        self.assertIn("<p>DMARC: monitoring", corps)
        self.assertNotIn("&lt;p&gt;", corps)

    def _dmarc_suivi(self, nom, **vals):
        return self._domaine(nom, dmarc_managed=True, dmarc_policy="reject",
                             dmarc_rua="dmarc@exemple.test", **vals)

    def _lookup(self, valeur):
        Domain = type(self.env["hosting.domain"])
        if isinstance(valeur, Exception):
            def faux(nom):
                raise valeur
        else:
            def faux(nom):
                return valeur
        return patch.object(Domain, "_dmarc_doh_lookup", staticmethod(faux))

    def test_dmarc_txt_observe_markup_valeur_echappee_langue_de_la_societe(self):
        """hosting_domain.py:430 : <code> réel, valeur TXT échappée, langue de la société."""
        self._societe("fr_CA")
        dom = self._dmarc_suivi("observe-l9b.test")
        txt = "v=DMARC1; p=reject; rua=mailto:dmarc@exemple.test; x=<b>"
        with self._lookup(txt):
            dom.with_env(self._env("en_CA"))._dmarc_run_check(post_chatter=True)
        corps = self._note_contient(dom, "DMARC : TXT observé via")
        self.assertIn("<code>", corps)
        self.assertIn("x=&lt;b&gt;</code>", corps)
        self.assertNotIn("<b>", corps)

    def test_dmarc_echec_dns_dans_la_langue_de_la_societe(self):
        """hosting_domain.py:414 : note d'échec de la vérification DNS."""
        self._societe("en_CA")
        dom = self._dmarc_suivi("echec-l9b.test")
        with self._lookup(OSError("délai l9b")):
            dom.with_env(self._env("fr_CA"))._dmarc_run_check(post_chatter=True)
        self._note_contient(dom, "DMARC: DNS check failed via")

    def test_dmarc_aucun_enregistrement_markup_dans_la_langue_de_la_societe(self):
        """hosting_domain.py:439."""
        self._societe("fr_CA")
        dom = self._dmarc_suivi("absent-l9b.test")
        with self._lookup(None):
            dom.with_env(self._env("en_CA"))._dmarc_run_check(post_chatter=True)
        self._note_contient(
            dom, "aucun enregistrement <code>_dmarc.absent-l9b.test</code> trouvé")

    def test_dmarc_drift_puis_retour_dans_la_langue_de_la_societe(self):
        """hosting_domain.py:458 (drift, <code> réels) et :450 (alignement rétabli)."""
        self._societe("fr_CA")
        dom = self._dmarc_suivi("drift-l9b.test")
        with self._lookup("v=DMARC1; p=none; rua=mailto:dmarc@exemple.test"):
            dom.with_env(self._env("en_CA"))._dmarc_run_check(post_chatter=False)
        self.assertTrue(dom.dmarc_drift)
        corps = self._note_contient(dom, "DMARC : drift détecté.")
        self.assertIn("Attendu : <code>v=DMARC1; p=reject;", corps)
        dom.dmarc_drift_notified = True
        with self._lookup(dom.dmarc_dns_value_expected):
            dom.with_env(self._env("en_CA"))._dmarc_run_check(post_chatter=False)
        self._note_contient(dom, "DMARC : alignement rétabli avec la valeur attendue.")

    def test_expiration_domaine_et_ssl_activites_dans_l_env_de_l_assigne(self):
        """hosting_domain.py:504 et :541 : activités dans la langue de l'assigné, créées
        dans son env (objet de l'avis aussi) ; :575 : alerte ntfy, langue de la société."""
        self._societe("en_CA")
        today = fields.Date.today()
        dom_fr = self._domaine("expire-fr-l9b.test", user_id=self.user_fr.id,
                               date_expiration=today + timedelta(days=30))
        dom_en = self._domaine("expire-en-l9b.test", user_id=self.user_en.id,
                               date_expiration=today + timedelta(days=31),
                               ssl_type="letsencrypt", ssl_expiry_date=today + timedelta(days=10))
        with self._ntfy() as envois:
            # Déclencheur : un env français (OdooBot de BF) ; un assigné anglophone.
            self.env["hosting.domain"].with_env(self._env("fr_CA"))._cron_check_domain_expirations()
        act_fr = dom_fr.activity_ids
        self.assertEqual(act_fr.summary, "Le domaine expire dans 30 jours")
        self.assertIn("Veuillez procéder au renouvellement", str(act_fr.note))
        act_en = dom_en.activity_ids.sorted("id")
        self.assertEqual(act_en.mapped("summary"),
                         ["Domain expires in 31 days", "SSL certificate expires in 10 days"])
        self.assertIn("Please proceed with the renewal", str(act_en[1].note))
        # L'objet de l'avis se traduit sur l'env de l'activité : celui de l'assigné.
        for sujet in self._sujets_avis(dom_en, "expires in"):
            self.assertIn("assigned to you", sujet)
        for sujet in self._sujets_avis(dom_fr, "Le domaine expire dans 30 jours"):
            self.assertNotIn("assigned to you", sujet)
        # Les domaines de la base copiée peuvent déjà être expirés : les deux titres anglais.
        self.assertIn(envois[0]["title"], (
            "DOMAINS/SSL: expirations detected",
            "DOMAINS/SSL: expirations detected — domains already expired"))
        self.assertIn("Domains close to expiry: ", envois[0]["body"])

    def test_dmarc_quotidien_note_activites_et_ntfy_dans_la_bonne_langue(self):
        """hosting_domain.py:640/649 (note au fil, société), :645/662/690 (activités dans
        l'env de l'assigné), ntfy (société)."""
        self._societe("fr_CA")
        self.env["ir.config_parameter"].sudo().set_param("hosting.dmarc_ntfy_enabled", "1")
        today = fields.Date.today()
        dom = self._dmarc_suivi("quotidien-l9b.test", user_id=self.user_en.id,
                                dmarc_next_review_date=today)
        with self._ntfy() as envois, \
                self._lookup("v=DMARC1; p=none; rua=mailto:dmarc@exemple.test"):
            self.env["hosting.domain"].with_env(self._env("en_CA"))._cron_dmarc_check()
        corps = self._note_contient(dom, "DMARC : drift détecté lors de la vérification quotidienne.")
        self.assertIn("<code>", corps)
        drift = dom.activity_ids.filtered(
            lambda a: a.activity_type_id == self.env.ref(f"{MODULE}.mail_activity_type_dmarc_drift"))
        self.assertIn("Expected: v=DMARC1; p=reject;", str(drift.note))
        self.assertIn("<br>", str(drift.note))
        revue = dom.activity_ids.filtered(
            lambda a: a.activity_type_id == self.env.ref(f"{MODULE}.mail_activity_type_dmarc_review"))
        self.assertEqual(revue.summary, "DMARC: policy review — quotidien-l9b.test")
        for sujet in self._sujets_avis(dom, "quotidien-l9b.test: DMARC"):
            self.assertIn("assigned to you", sujet)
        self.assertEqual(envois[-1]["title"], "DMARC : drift détecté")
        self.assertIn("Drifts DMARC : ", envois[-1]["body"])

    def test_dmarc_quotidien_activite_francaise_pour_un_assigne_francophone(self):
        """hosting_domain.py:662 : note de l'activité de drift dans la langue de l'assigné."""
        self._societe("en_CA")
        dom = self._dmarc_suivi("quotidien-fr-l9b.test", user_id=self.user_fr.id)
        with self._lookup("v=DMARC1; p=none; rua=mailto:dmarc@exemple.test"):
            self.env["hosting.domain"].with_env(self._env("en_CA"))._cron_dmarc_check()
        self.assertIn("Attendu : v=DMARC1; p=reject;", str(dom.activity_ids.note))
        self._note_contient(dom, "DMARC: drift detected during the daily check.")
        for sujet in self._sujets_avis(dom, "DMARC drift — quotidien-fr-l9b.test"):
            self.assertNotIn("assigned to you", sujet)

    def test_cloudflare_notes_au_fil_dans_la_langue_de_la_societe(self):
        """hosting_domain.py:831 : notes au fil de la synchro Cloudflare (création et mise
        à jour), langue de la société."""
        self._societe("fr_CA")
        Domain = self.env["hosting.domain"].with_env(self._env("en_CA"))
        self.assertEqual(Domain._sync_single_cloudflare_domain(
            {"expires_at": "2031-05-01T00:00:00Z", "auto_renew": False}, "cf-l9b.test"), "created")
        dom = self.env["hosting.domain"].search([("name", "=", "cf-l9b.test")])
        self._note_contient(dom, "Domaine créé automatiquement depuis la synchronisation Cloudflare.")
        self.assertEqual(Domain._sync_single_cloudflare_domain(
            {"expires_at": "2031-06-01T00:00:00Z", "auto_renew": True}, "cf-l9b.test"), "updated")
        self._note_contient(dom, "Synchronisation Cloudflare — Expiration : 2031-06-01, "
                                 "Renouvellement auto : Oui")

    # ============================================================ Rapports DMARC
    def test_rapport_dmarc_nom_de_repli_dans_la_langue_de_la_societe(self):
        """hosting_dmarc_report.py:96 : nom STOCKÉ, repli dans la langue de la société."""
        self._societe("en_CA")
        self.assertEqual(
            self.env["hosting.dmarc.report"].with_env(self._env("fr_CA")).new({}).name,
            "DMARC report")
        self._societe("fr_CA")
        self.assertEqual(
            self.env["hosting.dmarc.report"].with_env(self._env("en_CA")).new({}).name,
            "Rapport DMARC")

    def test_rapport_dmarc_ntfy_dans_la_langue_de_la_societe(self):
        """hosting_dmarc_report.py:332 : alerte d'ingestion, langue de la société."""
        self.env["ir.config_parameter"].sudo().set_param("hosting.dmarc_ntfy_enabled", "1")
        self.env["hosting.dmarc.report"].create(
            {"org_name": "Org L9B", "report_id": "rid-l9b-1", "domain_name": "l9b.test"})
        self._societe("fr_CA")
        with self._ntfy() as envois:
            self.env["hosting.dmarc.report"].with_env(self._env("en_CA"))._dmarc_ingest_notify(1)
        self.assertEqual(envois[0]["title"], "DMARC : rapports ingérés")
        self.assertTrue(envois[0]["body"].startswith("Nouveaux rapports DMARC : 1"))

    # =========================================================== Sécurité, audit
    def _evenement(self, lang):
        return self.env["hosting.security.event"].with_env(self._env(lang)).create({
            "name": f"Fuite L9B {lang}", "event_type": "data_breach", "severity": "critical",
            "service_ids": [Command.link(self.service.id)],
        })

    def test_evenement_securite_audit_et_ntfy_dans_la_langue_de_la_societe(self):
        """hosting_security_event.py:134 : audit, ntfy et libellés de sélection traduits,
        langue de la société."""
        self._societe("en_CA")
        with self._ntfy() as envois:
            ev = self._evenement("fr_CA")
        self.assertEqual(self._audit(ev._name, ev.id)[:1].description,
                         f"Security event created: {ev.name}")
        self.assertEqual(envois[0]["title"], f"SECURITY [CRITICAL]: {ev.name}")
        self.assertIn("Type: Data breach", envois[0]["body"])
        self.assertIn("Severity: Critical", envois[0]["body"])
        self._societe("fr_CA")
        with self._ntfy() as envois:
            ev_fr = self._evenement("en_CA")
        self.assertEqual(self._audit(ev_fr._name, ev_fr.id)[:1].description,
                         f"Événement de sécurité créé : {ev_fr.name}")
        self.assertEqual(envois[0]["title"], f"SÉCURITÉ [CRITIQUE] : {ev_fr.name}")
        self.assertIn("Type : Fuite de données", envois[0]["body"])
        self.assertIn("Sévérité : Critique", envois[0]["body"])

    def test_journal_audit_nom_stocke_dans_la_langue_de_la_societe(self):
        """hosting_audit_log.py:154 : display_name STOCKÉ, libellé traduit, société."""
        AuditLog = self.env["hosting.audit.log"]
        self._societe("fr_CA")
        entree = AuditLog.with_env(self._env("en_CA"))._log_event(
            action_type="config_change", category="config", description="l9b",
            res_name="Objet L9B")
        self.assertEqual(entree.display_name, "Changement de configuration - Objet L9B")
        self._societe("en_CA")
        entree = AuditLog.with_env(self._env("fr_CA"))._log_event(
            action_type="config_change", category="config", description="l9b",
            res_name="Objet L9B")
        self.assertEqual(entree.display_name, "Configuration change - Objet L9B")

    def test_journal_audit_usererror_dans_la_langue_de_l_usager(self):
        """hosting_audit_log.py:164, :167, :216 : refus montrés à l'usager, sa langue."""
        entree = self.env["hosting.audit.log"]._log_event(
            action_type="access", category="access", description="l9b")
        cas = [
            (lambda e: entree.with_env(e).write({"description": "x"}),
             "Audit log entries cannot be modified.",
             "Les entrées du journal d'audit ne peuvent pas être modifiées."),
            (lambda e: entree.with_env(e).unlink(),
             "Audit log entries cannot be deleted.",
             "Les entrées du journal d'audit ne peuvent pas être supprimées."),
            (lambda e: e["hosting.audit.log"].create({"description": "x"}),
             "Audit log entries can only be created by the system.",
             "Les entrées du journal d'audit ne peuvent être créées que par le système."),
        ]
        for appel, anglais, francais in cas:
            with self.assertRaises(UserError) as en:
                appel(self._env("en_CA"))
            self.assertEqual(en.exception.args[0], anglais)
            with self.assertRaises(UserError) as fr:
                appel(self._env("fr_CA"))
            self.assertEqual(fr.exception.args[0], francais)

    # =============================================================== Licences
    def test_licence_audit_champ_sensible_dans_la_langue_de_la_societe(self):
        """hosting_license.py:214 : journal d'audit, langue de la société."""
        self._societe("en_CA")
        lic = self.env["hosting.license"].with_env(self._env("fr_CA")).create(
            {"name": "Licence L9B", "jwt_token": "jeton-l9b"})
        self.assertEqual(
            self._audit(lic._name, lic.id)[:1].description,
            "Sensitive field changed on license Licence L9B: jwt_token (set). Value not logged.")

    def test_siege_audit_champ_sensible_et_nom_dans_la_langue_de_la_societe(self):
        """hosting_license_seat.py:148 : description et nom de repli « Siège #n »."""
        lic = self.env["hosting.license"].create({"name": "Pool L9B"})
        self._societe("fr_CA")
        siege = self.env["hosting.license.seat"].with_env(self._env("en_CA")).create(
            {"license_id": lic.id, "product_key": "CLE-L9B"})
        entree = self._audit(siege._name, siege.id)[:1]
        self.assertEqual(
            entree.description,
            f"Champ sensible modifié sur le siège #{siege.id} (Pool L9B) : product_key (set). "
            "Valeur non journalisée.")
        self.assertEqual(entree.res_name, f"Siège #{siege.id}")
        self.assertEqual(entree.display_name, f"Changement de configuration - Siège #{siege.id}")

    def _alerte_licences(self, destinataire, lang_declencheur):
        client = self.env["res.partner"].create(
            {"name": f"Proprio L9B {destinataire.lang}", "is_company": True})
        self.env["hosting.service"].create({
            "name": f"Service proprio {destinataire.lang}", "partner_id": client.id,
            "software_id": self.logiciel.id, "user_id": destinataire.id,
        })
        self.env["hosting.license"].create({
            "name": "Licence expirante", "owner_partner_id": client.id,
            "expiry_date": fields.Date.today() + timedelta(days=12),
        })
        self.env["hosting.license"].with_env(self._env(lang_declencheur))._cron_expiry_alerts()
        courriel = self.env["mail.mail"].search(
            [("email_to", "=", destinataire.email)], order="id desc", limit=1)
        self.assertTrue(courriel, "aucun courriel d'expiration créé")
        return courriel, client

    def test_licences_courriel_d_expiration_dans_la_langue_du_destinataire(self):
        """hosting_license.py:349 : courriel dans la langue du destinataire (user_env)."""
        courriel, client = self._alerte_licences(self.user_fr, "en_CA")
        self.assertEqual(courriel.subject,
                         f"[Licences] {client.name} — 1 expiration(s) ≤ 90 j")
        corps = str(courriel.body_html)
        self.assertIn("Bonjour Francine EssaiB,", corps)
        self.assertIn("12 j", corps)
        self.assertIn("Éditeur", corps)
        self.assertIn("Licences appartenant à", corps)
        courriel, client = self._alerte_licences(self.user_en, "fr_CA")
        self.assertEqual(courriel.subject,
                         f"[Licenses] {client.name} — 1 expiration(s) ≤ 90 d")
        corps = str(courriel.body_html)
        self.assertIn("Hello Edward EssaiB,", corps)
        self.assertIn("12 d", corps)
        self.assertIn("Vendor", corps)

    def test_licences_courriel_d_expiration_balise_du_proprietaire_non_echappee(self):
        """hosting_license.py:376 : l'intro garde son <strong> réel (le nom, lui, échappé).
        ⚠️ `renv._(…, owner=_esc(nom))` : un argument Markup fait échapper TOUTE la
        traduction par `_()`, et la balise s'affiche en texte dans le courriel."""
        courriel, client = self._alerte_licences(self.user_fr, "en_CA")
        corps = str(courriel.body_html)
        self.assertIn(f"<strong>{client.name}</strong>", corps)
        self.assertNotIn("&lt;strong&gt;", corps)

    # ============================================================ Maintenances
    def _planification(self, assigne, lang_declencheur, **vals):
        return self.env["hosting.maintenance.schedule"].with_env(
            self._env(lang_declencheur)).create({
                "name": f"Plan L9B {assigne.lang}", "service_id": self.service.id,
                "maintenance_type": "security_patch", "frequency": "weekly",
                "user_id": assigne.id, "instructions": "Ligne 1\nLigne 2",
                "last_performed": fields.Date.today() - timedelta(days=3), **vals,
            })

    def test_maintenance_libelles_de_selection_traduits(self):
        """hosting_maintenance_schedule.py:352/359 : libellés traduits sur self.env."""
        plan = self._planification(self.user_en, "en_CA")
        self.assertEqual(plan.with_env(self._env("fr_CA"))._get_type_display(),
                         "Correctifs de sécurité")
        self.assertEqual(plan.with_env(self._env("fr_CA"))._get_frequency_display(),
                         "Hebdomadaire")
        self.assertEqual(plan.with_env(self._env("en_CA"))._get_type_display(),
                         "Security patches")
        self.assertEqual(plan.with_env(self._env("en_CA"))._get_frequency_display(), "Weekly")

    def test_maintenance_activite_dans_l_env_de_l_assigne(self):
        """hosting_maintenance_schedule.py:153 : note dans la langue de l'assigné, activité
        créée dans son env (objet de l'avis)."""
        plan_fr = self._planification(self.user_fr, "en_CA")
        note = str(plan_fr.activity_ids.note)
        self.assertIn("Type : Correctifs de sécurité", note)
        self.assertIn("Fréquence : Hebdomadaire", note)
        self.assertIn("Instructions :", note)
        for sujet in self._sujets_avis(plan_fr, "Plan L9B fr_CA"):
            self.assertNotIn("assigned to you", sujet)
        plan_en = self._planification(self.user_en, "fr_CA")
        note = str(plan_en.activity_ids.note)
        self.assertIn("Type: Security patches", note)
        self.assertIn("Frequency: Weekly", note)
        for sujet in self._sujets_avis(plan_en, "Plan L9B en_CA: Plan L9B en_CA"):
            self.assertIn("assigned to you", sujet)

    def test_maintenance_faite_audit_et_fil_dans_la_langue_de_la_societe(self):
        """hosting_maintenance_schedule.py:279 : audit, retour d'activité et note au fil,
        langue de la société."""
        self._societe("en_CA")
        plan = self._planification(self.user_fr, "fr_CA")
        plan.with_env(self._env("fr_CA")).action_mark_done()
        today = fields.Date.today()
        self.assertEqual(
            self._audit(plan._name, plan.id)[:1].description,
            f"Maintenance completed: {plan.name} (service: {self.service.name})")
        self._note_contient(plan, f"Maintenance task completed on {today}")
        self._note_contient(plan, f"Completed on {today}")

    # =============================================================== Logiciels
    def _logiciel_sans_depot(self, code):
        return self.env["hosting.software"].create({
            "name": f"Sans dépôt {code}", "code": code, "software_type": "self_hosted",
            "version_check_method": "github", "github_repo": False,
        })

    def test_logiciel_erreur_de_verification_stockee_dans_la_langue_de_la_societe(self):
        """hosting_software.py:214 (_version_error_env) : `version_check_error` STOCKÉ,
        langue de la société."""
        self._societe("en_CA")
        sw = self._logiciel_sans_depot("L9B-E1")
        sw.with_env(self._env("fr_CA"))._check_version()
        self.assertEqual(sw.version_check_error, "GitHub repository not configured")
        self._societe("fr_CA")
        sw.with_env(self._env("en_CA"))._check_version()
        self.assertEqual(sw.version_check_error, "Dépôt GitHub non configuré")

    def test_logiciel_notification_dans_la_langue_de_l_usager(self):
        """hosting_software.py:194 : notification montrée à l'usager qui clique."""
        self._societe("fr_CA")
        sw = self._logiciel_sans_depot("L9B-E2")
        params = sw.with_env(self._env("en_CA")).action_check_version()["params"]
        self.assertEqual(params["title"], "Version check completed")
        self.assertEqual(params["message"], "Error: Dépôt GitHub non configuré")
        params = sw.with_env(self._env("fr_CA")).action_check_version()["params"]
        self.assertEqual(params["title"], "Vérification de version terminée")
        self.assertEqual(params["message"], "Erreur : Dépôt GitHub non configuré")

    def test_logiciel_ntfy_nouvelles_versions_dans_la_langue_de_la_societe(self):
        """hosting_software.py:593 : alerte ntfy du travail planifié, société."""
        self._societe("fr_CA")
        sw = self.env["hosting.software"].create({
            "name": "Versionné L9B", "code": "L9B-V", "software_type": "self_hosted",
            "version_check_method": "github", "github_repo": "exemple/l9b",
            "latest_version": "1.0.0",
        })
        Software = type(self.env["hosting.software"])

        def verifier(self_):
            if self_ == sw:
                self_.latest_version = "2.0.0"

        with self._ntfy() as envois, patch.object(Software, "_check_version", verifier), \
                patch.object(self.env.cr, "commit", lambda: None):
            self.env["hosting.software"].with_env(self._env("en_CA"))._cron_check_versions()
        self.assertEqual(envois[0]["title"], "VERSIONS : 1 nouvelle(s) version(s) détectée(s)")
        self.assertIn("Versionné L9B: 1.0.0 → 2.0.0", envois[0]["body"])

    # ===================================================== Noms stockés, réglages
    def test_releve_saas_nom_stocke_dans_la_langue_de_la_societe(self):
        """hosting_saas_reading.py:114 : nom STOCKÉ, langue de la société."""
        Reading = self.env["hosting.saas.backup.reading"]
        self._societe("fr_CA")
        rel = Reading.with_env(self._env("en_CA")).create({
            "provider": "cubebackup", "period_start": date(2031, 1, 1),
            "period_end": date(2031, 1, 31)})
        self.assertEqual(rel.display_name, "CubeBackup 2031-01-01 au 2031-01-31")
        self._societe("en_CA")
        rel = Reading.with_env(self._env("fr_CA")).create({
            "provider": "cubebackup", "period_start": date(2031, 2, 1),
            "period_end": date(2031, 2, 28)})
        self.assertEqual(rel.display_name, "CubeBackup 2031-02-01 to 2031-02-28")

    def test_journal_mise_a_jour_nom_de_repli_dans_la_langue_de_la_societe(self):
        """hosting_update_log.py:67 : nom STOCKÉ, repli dans la langue de la société."""
        self._societe("en_CA")
        self.assertEqual(
            self.env["hosting.update.log"].with_env(self._env("fr_CA")).new({}).display_name,
            "Update")
        self._societe("fr_CA")
        self.assertEqual(
            self.env["hosting.update.log"].with_env(self._env("en_CA")).new({}).display_name,
            "Mise à jour")

    def test_reglages_fuseaux_horaires_dans_la_langue_de_l_usager(self):
        """res_config_settings.py:179 : sélection construite par méthode, env._ (usager)."""
        Settings = self.env["res.config.settings"]
        en = dict(Settings.with_env(self._env("en_CA"))._get_timezone_selection())
        fr = dict(Settings.with_env(self._env("fr_CA"))._get_timezone_selection())
        self.assertEqual(en["America/Toronto"], "America/Toronto (Eastern)")
        self.assertEqual(fr["America/Toronto"], "America/Toronto (Est)")

    # ================================================================= Routes
    def _requete(self, lang, donnees, entetes=None):
        fausse = MagicMock()
        fausse.env = self._env(lang)
        fausse.httprequest.data = json.dumps(donnees).encode()
        fausse.httprequest.headers = entetes or {}
        fausse.make_json_response = lambda data, status=200, **k: dict(data, _status=status)
        return fausse

    def test_route_sauvegarde_notes_dans_la_langue_de_la_societe(self):
        """controllers/backup_api.py:142 (ancien format) et :244 (Restic) : note STOCKÉE
        écrite par une route publique, langue de la société."""
        self._societe("fr_CA")
        Run = type(self.env["hosting.backup.run"])
        ctrl = backup_api.BackupAPIController()
        for type_rapport in ("legacy", "restic"):
            donnees = {"report_type": type_rapport, "hostname": f"route-{type_rapport}-l9b",
                       "timestamp": "2031-03-04 05:06:07", "results": []}
            with patch.object(backup_api, "request", self._requete("en_CA", donnees)), \
                    patch.object(Run, "action_send_report", lambda self_: True):
                rep = ctrl._dispatch_backup_report()
            self.assertTrue(rep.get("success"), rep)
            run = self.env["hosting.backup.run"].browse(rep["backup_run_id"])
            self.assertEqual(run.notes, "Reçu par l'API le 2031-03-04 05:06:07", type_rapport)

    def test_route_provisionnement_textes_dans_la_langue_de_la_societe(self):
        """controllers/provisioning_api.py:132 : contact créé, note au fil (Markup) et
        audit d'une route publique, langue de la société."""
        self.env["ir.config_parameter"].sudo().set_param(
            "hosting.provision_api_token", "jeton-l9b")
        self._societe("fr_CA")
        donnees = {"slug": "prov-l9b", "client_name": "Client Provision L9B",
                   "software_code": "L9B", "status": "success", "log": "ok"}
        requete = self._requete("en_CA", donnees, {"X-Provision-Token": "jeton-l9b"})
        ctrl = provisioning_api.ProvisioningAPIController()
        route = provisioning_api.ProvisioningAPIController.receive_provision_report
        with patch.object(provisioning_api, "request", requete):
            rep = getattr(route, "original_endpoint", route)(ctrl)
        self.assertTrue(rep.get("success"), rep)
        partenaire = self.env["res.partner"].browse(rep["partner_id"])
        self.assertIn("Auto-créé via le script de provisionnement (L9B)", str(partenaire.comment))
        service = self.env["hosting.service"].browse(rep["service_id"])
        corps = self._note_contient(service, "<strong>Provisionnement success</strong> (created)")
        self.assertIn("Logiciel : Logiciel EssaiB (L9B)", corps)
        self.assertIn("Serveur : (non lié)", corps)
        self.assertEqual(
            self._audit("hosting.service", service.id)[:1].description,
            "Locataire prov-l9b created via le script de provisionnement (L9B)")

    # ============================================================== Migration
    def _poser_brut(self, rec, champ, valeurs):
        """Écrit la valeur jsonb telle qu'une base d'avant 18.0.2.63.0 la porte."""
        porteur = rec.ir_actions_server_id if rec._name == "ir.cron" else rec
        self.env.flush_all()
        self.env.cr.execute(SQL("UPDATE %s SET %s = %s WHERE id = %s",
                                SQL.identifier(porteur._table), SQL.identifier(champ),
                                Json(valeurs), porteur.id))
        self.env.invalidate_all()
        return porteur

    def _lire_brut(self, porteur, champ):
        self.env.flush_all()
        self.env.cr.execute(SQL("SELECT %s FROM %s WHERE id = %s", SQL.identifier(champ),
                                SQL.identifier(porteur._table), porteur.id))
        return self.env.cr.fetchone()[0]

    def _migrer(self, avec_post=False):
        if avec_post:
            _migration("post-migrate.py").migrate(self.env.cr, "18.0.2.62.1")
        _migration("end-migrate.py").migrate(self.env.cr, "18.0.2.62.1")
        self.env.invalidate_all()

    def test_migration_texte_livre_francais_passe_a_l_anglais_et_garde_le_francais(self):
        """migrations/18.0.2.63.0 : travail planifié et type d'activité au texte LIVRÉ
        français : en_US devient l'anglais du fichier de données, fr_CA garde le français."""
        fin = _migration("end-migrate.py")
        cron = self.env.ref(f"{MODULE}.hosting_audit_log_archive_cron")
        fr_cron = fin.LIVRES_FR[("data/hosting_audit_cron.xml",
                                 "hosting_audit_log_archive_cron", "name")]
        act = self.env.ref(f"{MODULE}.mail_activity_type_hosting_maintenance")
        fr_act = fin.LIVRES_FR[("data/mail_activity_type.xml",
                                "mail_activity_type_hosting_maintenance", "name")]
        p_cron = self._poser_brut(cron, "name", {"en_US": fr_cron})
        p_act = self._poser_brut(act, "name", {"en_US": fr_act})
        self._migrer()
        brut = self._lire_brut(p_cron, "name")
        self.assertEqual(brut["en_US"], "Hosting: archive old audit logs")
        self.assertEqual(brut["fr_CA"], fr_cron)
        brut = self._lire_brut(p_act, "name")
        self.assertEqual(brut["en_US"], "Hosting maintenance")
        self.assertEqual(brut["fr_CA"], fr_act)
        self.assertEqual(cron.with_context(lang="en_US").name, "Hosting: archive old audit logs")
        self.assertEqual(act.with_context(lang="fr_CA").name, fr_act)

    def test_migration_retouche_laissee_et_recopiee(self):
        """migrations/18.0.2.63.0 : une retouche en_US est laissée (end) et recopiée dans
        les langues sans clé (post), où le français livré ne la masque pas."""
        cron = self.env.ref(f"{MODULE}.hosting_audit_log_archive_cron")
        act = self.env.ref(f"{MODULE}.mail_activity_type_hosting_update")
        p_cron = self._poser_brut(cron, "name", {"en_US": "Archivage maison L9B"})
        p_act = self._poser_brut(act, "summary", {"en_US": "Résumé retouché L9B"})
        self._migrer(avec_post=True)
        brut = self._lire_brut(p_cron, "name")
        self.assertEqual(brut["en_US"], "Archivage maison L9B")
        self.assertEqual(brut["fr_CA"], "Archivage maison L9B")
        brut = self._lire_brut(p_act, "summary")
        self.assertEqual(brut["en_US"], "Résumé retouché L9B")
        self.assertEqual(brut["fr_CA"], "Résumé retouché L9B")

    def test_migration_notes_terme_a_terme_gardent_le_francais(self):
        """migrations/18.0.2.63.0 (end-migrate, écriture SQL de la seule clé en_US) : les
        notes d'un logiciel livré (html_translate) passent à l'anglais en en_US sans que le
        français de fr_CA soit remplacé par l'anglais."""
        fin = _migration("end-migrate.py")
        sw = self.env.ref(f"{MODULE}.software_calcom")
        francais = fin.LIVRES_FR[("data/hosting_software_extended.xml", "software_calcom", "notes")]
        porteur = self._poser_brut(sw, "notes", {"en_US": francais, "fr_CA": francais})
        self._migrer()
        brut = self._lire_brut(porteur, "notes")
        self.assertEqual(brut["en_US"], "<p>Open source scheduling infrastructure for everyone.</p>")
        self.assertEqual(brut["fr_CA"], francais)
        self.assertIn("Infrastructure de planification", str(sw.with_context(lang="fr_CA").notes))
