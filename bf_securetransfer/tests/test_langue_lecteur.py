"""Chaque texte du transfert sécurisé dans la langue de QUI LE LIRA.

La source du module est passée du français à l'anglais (le français vit dans
`i18n/fr_CA.po`, et Odoo ne traduit jamais vers en_US). Le passage a aussi corrigé le
code : un texte écrit ou envoyé suivait la langue de qui déclenchait l'action
(visiteur anonyme, cron, opérateur) au lieu de celle de son lecteur.

Ce que ces essais tiennent :

* la page publique d'un visiteur anglophone est anglaise, même quand seule en_US
  est active (`active_lang`, `_visitor_lang`, `_locale_of`), et ses textes hors
  QWeb (`_page_texts`) suivent le visiteur ;
* les erreurs JSON de l'API de téléversement parlent la langue de la page ;
* le bureau d'abus lit dans la langue de son contact, sinon de la société ; chaque
  destinataire, dans la sienne ; chaque gestionnaire reçoit son activité dans sa
  langue, créée dans son environnement (l'avis d'activité prend la langue de
  l'appelant) ;
* l'avis de nouveau visiteur part dans la langue de l'EXPÉDITEUR, le code demandé
  depuis la page par un inconnu dans la langue de la page ;
* les notes du journal d'accès sont écrites dans la langue du transfert
  (`_log_env`), et le certificat se rend dans cette langue (`t-lang`) ;
* le libellé de la page de dépôt garde EXACTEMENT « Envoi de fichiers | %s » en
  français (sinon chaque page française serait renommée à la synchro suivante) ;
* les verdicts des sondes S3, l'export CSV et l'activité de purge en échec ;
* les tailles de fichiers (page publique, courriels) avec l'unité du lecteur ;
* la migration 18.0.1.22.0 des corps de courriel livrés.

Partout, le lecteur et l'appelant ont des langues DIFFÉRENTES, épinglées
(contexte, contacts et usagers créés en fr_CA ou en_CA) : sur une base francophone,
OdooBot et l'admin sont en fr_CA, et `_()` sans langue au contexte prend la leur.
Le texte attendu est le plus souvent l'anglais : remettre le code d'origine (un
littéral français) ou retirer l'épinglage de la langue fait tomber l'essai.

Aucun réseau : S3 est bouchonné, `MailMail.send` est neutralisé (les `mail.mail`
sont créés et lus, jamais remis).
"""
import base64
import importlib.util
import json
import os
import re
import types
from datetime import timedelta
from unittest.mock import MagicMock, patch

from lxml import etree
from markupsafe import Markup

from odoo import fields
from odoo.tests import HttpCase, TransactionCase, tagged
from odoo.tools.misc import format_date

from odoo.addons.bf_securetransfer.controllers import main as st_main
from odoo.addons.bf_securetransfer.models import s3
from odoo.addons.bf_securetransfer.models.secure_transfer import active_lang
from odoo.addons.bf_securetransfer.models.secure_transfer_file import size_label

S3_MOD = "odoo.addons.bf_securetransfer.models.s3"
MAIN_MOD = "odoo.addons.bf_securetransfer.controllers.main"
MAIL_SEND = "odoo.addons.mail.models.mail_mail.MailMail.send"
MODULE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CODE_RE = re.compile(r">(\d{6})</span>")
GB, MB = 1073741824, 1048576
MAIL_DATA = os.path.join(MODULE_DIR, "data", "secure_transfer_mail_template.xml")
# Une unité française collée à un nombre : « 4 Ko », « 2 Go », « 512 o ».
UNITE_FR_RE = re.compile(r"\d (Go|Mo|Ko|o)\b")


def _livre(gabarit, champ):
    """La valeur anglaise du fichier de données, sous la forme qu'Odoo range
    (un champ HTML est assaini à l'écriture)."""
    xmlid = gabarit.get_external_id()[gabarit.id].split(".")[1]
    noeud = etree.parse(MAIL_DATA).find(
        ".//record[@id='%s']/field[@name='%s']" % (xmlid, champ))
    if noeud.get("type") != "html":
        return (noeud.text or "").strip()
    brut = "".join(etree.tostring(n, method="html", encoding="unicode") for n in noeud)
    return str(gabarit._fields[champ].convert_to_column(brut, gabarit)).strip()

_LIMITERS = (
    st_main._create_limiter,
    st_main._finalize_limiter,
    st_main._token_fail_limiter,
    st_main._password_fail_limiter,
    st_main._report_limiter,
    st_main._otp_fail_limiter,
    st_main._otp_send_limiter,
    st_main._otp_cooldown_limiter,
    st_main._upload_ops_limiter,
)


def _preparer(cls):
    """Langues, catalogue français, réglages et marque communs aux classes."""
    for code in ("fr_CA", "en_CA"):
        cls.env["res.lang"]._activate_lang(code)
    # Les gabarits QWeb (pages, certificat) se traduisent depuis la base.
    cls.env["ir.module.module"]._load_module_terms(
        ["bf_securetransfer"], ["fr_CA"], overwrite=True)
    icp = cls.env["ir.config_parameter"].sudo()
    for key, value in {
        "quota_daily_transfers_per_ip": "500",
        "quota_daily_transfers_per_sender": "500",
        "quota_daily_bytes_per_ip_mb": "1000000",
        "rate_create_per_hour": "500",
        "public_upload_enabled": "1",
        "require_recipient_otp": "0",
        "require_sender_otp": "0",
        "default_sender_allowlist": "",
        "default_recipient_allowlist": "",
        "autoprovision_user_pages": "0",
    }.items():
        icp.set_param("bf_securetransfer." + key, value)
    cls.brand = cls.env.ref("bf_securetransfer.brand_default")
    cls.brand.sudo().write({
        "watermark_downloads": False,
        "allow_open_audience": True,
        "audience_max_default": 50,
        "audience_domains": False,
        "max_retention_days": 30,
        "sender_allowlist": False,
        "recipient_allowlist": False,
    })


def _sans_en_ca(env):
    """La base n'a plus que fr_CA et en_US : le cas d'une base où l'anglais
    n'existe qu'en langue source. Par SQL : en_CA reste la langue de quelques
    usagers de la base copiée, et l'ORM refuse de la désactiver."""
    env["res.lang"]._activate_lang("en_US")
    env.flush_all()
    env.cr.execute("UPDATE res_lang SET active = false WHERE code = 'en_CA'")
    env.registry.clear_all_caches()
    env.invalidate_all()
    actives = [code for code, _name in env["res.lang"].get_installed()]
    assert "en_CA" not in actives and "en_US" in actives and "fr_CA" in actives, actives


def _tete(transfer):
    """head_object bouchonné : chaque clé du transfert existe à sa taille."""
    sizes = {f.s3_key: int(f.size) for f in transfer.file_ids}

    def _head(env, key):
        if key in sizes:
            return {"size": sizes[key], "etag": "etag-" + key[-8:]}
        return None
    return _head


class _FauxRequest:
    """Le strict nécessaire de `odoo.http.request` pour les fonctions du
    contrôleur : un environnement, un en-tête Accept-Language, update_context."""

    def __init__(self, env, entete=None):
        self.env = env
        self.httprequest = types.SimpleNamespace(
            headers={"Accept-Language": entete} if entete is not None else {})

    def update_context(self, **overrides):
        self.env = self.env(context=dict(self.env.context, **overrides))


class _Outils:
    """Fixtures partagées : transferts, usagers, courriels, notes."""

    _n = 0

    def _adresse(self, prefixe):
        type(self)._n += 1
        return "%s-essai-%d@example.com" % (prefixe, self._n)

    def _transfert(self, locale="en_CA", appelant="fr_CA", **vals):
        valeurs = {
            "sender_name": "Expéditrice Essai",
            "sender_email": self._adresse("exp"),
            "recipient_emails": self._adresse("dest"),
            "message": "Bonjour",
            "retention_days": 7,
        }
        valeurs.update(vals)
        Model = self.env["secure.transfer"].with_context(lang=appelant)
        return Model.api_create(
            self.brand, valeurs, "203.0.113.10", "agent-essai/1.0", locale)

    def _activer(self, transfer, password=None):
        transfer._register_file("rapport.pdf", 4096)
        with patch(S3_MOD + ".head_object", side_effect=_tete(transfer)):
            transfer.action_finalize(password=password)
        self.assertEqual(transfer.state, "active")
        return transfer

    def _contact(self, email, lang):
        return self.env["res.partner"].create({
            "name": "Contact %s" % email, "email": email, "lang": lang})

    def _usager(self, prefixe, lang, gestionnaire=False):
        groupes = [self.env.ref("base.group_user").id]
        if gestionnaire:
            groupes.append(
                self.env.ref("bf_securetransfer.group_securetransfer_manager").id)
        login = self._adresse(prefixe)
        return self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Usager %s" % login, "login": login, "email": login,
            "lang": lang, "groups_id": [(6, 0, groupes)],
        })

    def _courriels(self, email):
        self.env.invalidate_all()
        return self.env["mail.mail"].sudo().search(
            [("email_to", "=", email)], order="id")

    def _notes(self, transfer, action=None):
        self.env.invalidate_all()
        logs = transfer.sudo().access_log_ids.sorted("id")
        if action:
            logs = logs.filtered(lambda e: e.action == action)
        return [n or "" for n in logs.mapped("note")]

    def _note(self, transfer, action):
        notes = self._notes(transfer, action)
        self.assertTrue(notes, "aucune note « %s » au journal" % action)
        return notes[-1]

    def _avis_d_activite(self, transfer, user):
        """Le message « … assigned to you » posté à l'assigné d'une activité."""
        self.env.invalidate_all()
        return self.env["mail.message"].sudo().search([
            ("model", "=", "secure.transfer"), ("res_id", "=", transfer.id),
            ("message_type", "=", "user_notification"),
            ("partner_ids", "in", user.partner_id.ids),
        ])


@tagged("post_install", "-at_install")
class TestLangueDuLecteur(_Outils, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _preparer(cls)
        # Les actions d'opérateur exigent le groupe gestionnaire.
        cls.env.user.groups_id = [
            (4, cls.env.ref("bf_securetransfer.group_securetransfer_user").id),
            (4, cls.env.ref("bf_securetransfer.group_securetransfer_manager").id)]

    def setUp(self):
        super().setUp()
        p = patch(MAIL_SEND, lambda self, *a, **k: True)
        p.start()
        self.addCleanup(p.stop)

    # ------------------------------------------------- langue de la page publique
    def test_active_lang_prend_la_variante_active_de_la_meme_langue(self):
        """Si ça casse : sur une base où l'anglais n'existe qu'en en_US, un
        lecteur anglophone (en_CA demandé) retombe sur fr_CA et lit du français."""
        _sans_en_ca(self.env)
        self.assertEqual(active_lang(self.env, "en_CA"), "en_US")
        self.assertEqual(active_lang(self.env, "en_GB"), "en_US")
        self.assertEqual(active_lang(self.env, "fr_FR"), "fr_CA")
        self.assertEqual(active_lang(self.env, "fr_CA"), "fr_CA")

    def test_page_anglaise_quand_seule_en_us_est_active(self):
        """Si ça casse : le visiteur anglophone d'une base sans en_CA reçoit une
        page française (contexte fr_CA, chaînes de script fr_CA)."""
        _sans_en_ca(self.env)
        visiteur = _FauxRequest(self.env(context={}), "en-US,en;q=0.9")
        with patch(MAIN_MOD + ".request", visiteur):
            self.assertEqual(st_main._resolve_locale(env=self.env), "en_CA")
            self.assertEqual(st_main._visitor_lang(self.env), "en_US")
            locale = st_main._apply_locale()
        self.assertEqual(locale, "en_CA")
        self.assertEqual(visiteur.env.context.get("lang"), "en_US",
                         "la page se rend dans la langue anglaise ACTIVE")

    def test_page_suit_la_famille_de_la_langue_du_visiteur(self):
        """Si ça casse : les chaînes du script (`_UI_STRINGS`) et le texte QWeb
        de la page ne parlent plus la même langue."""
        for entete, contexte, locale in (
                ("en-GB,en;q=0.8", "en_CA", "en_CA"),
                ("en-CA", "en_CA", "en_CA"),
                ("fr-FR,fr;q=0.9", "fr_CA", "fr_CA"),
                ("fr-CA", "fr_CA", "fr_CA")):
            visiteur = _FauxRequest(self.env(context={}), entete)
            with patch(MAIN_MOD + ".request", visiteur):
                self.assertEqual(st_main._apply_locale(), locale, entete)
            self.assertEqual(visiteur.env.context.get("lang"), contexte, entete)
        self.assertEqual(st_main._locale_of("en_US"), "en_CA")
        self.assertEqual(st_main._locale_of("fr_FR"), "fr_CA")

    def test_textes_hors_qweb_de_la_page_dans_la_langue_du_visiteur(self):
        """Si ça casse : le <title>, les durées et la confirmation de
        signalement restent dans la langue de la source, quelle que soit la page."""
        en = st_main._page_texts(self.env(context={"lang": "en_CA"}))
        fr = st_main._page_texts(self.env(context={"lang": "fr_CA"}))
        self.assertEqual(en["send_files"], "Send files")
        self.assertEqual(en["shared_files"], "Shared files")
        self.assertEqual(en["days"] % 7, "7 days")
        self.assertEqual(en["report_confirm"], "Report this transfer as abusive?")
        self.assertEqual(fr["send_files"], "Envoyer des fichiers")
        self.assertEqual(fr["shared_files"], "Fichiers partagés")
        self.assertEqual(fr["secure_message"], "Message sécurisé")
        self.assertEqual(fr["expires_on"], "Expire le {date} (UTC).")
        self.assertEqual(fr["report_confirm"], "Signaler ce transfert comme abusif ?")

    # ------------------------------------------------------------ abus
    def test_activites_d_abus_dans_la_langue_de_chaque_gestionnaire(self):
        """Si ça casse : chaque gestionnaire lit l'activité (et son avis « assigné
        à vous ») dans la langue du visiteur anonyme qui a signalé."""
        anglo = self._usager("gest-en", "en_CA", gestionnaire=True)
        franco = self._usager("gest-fr", "fr_CA", gestionnaire=True)
        t = self._activer(self._transfert(locale="fr_CA"))
        visiteur = _FauxRequest(self.env(context={"lang": "fr_CA"}), "fr-CA")
        with patch(MAIN_MOD + ".request", visiteur):
            st_main._notify_abuse_managers(t, "pourriel", "198.51.100.7",
                                           verified=False)
        Activity = self.env["mail.activity"].sudo()
        act_en = Activity.search([("res_model", "=", "secure.transfer"),
                                  ("res_id", "=", t.id), ("user_id", "=", anglo.id)])
        act_fr = Activity.search([("res_model", "=", "secure.transfer"),
                                  ("res_id", "=", t.id), ("user_id", "=", franco.id)])
        self.assertEqual(act_en.summary, "Abuse report: %s" % t.name)
        self.assertIn("Abuse report received for transfer", act_en.note)
        self.assertIn("[unverified, to review]", act_en.note)
        self.assertIn("Reason: pourriel", act_en.note)
        self.assertEqual(act_fr.summary, "Signalement d'abus — %s" % t.name)
        self.assertIn("Motif : pourriel", act_fr.note)
        # L'avis d'assignation prend la langue de l'environnement qui CRÉE
        # l'activité : créée dans celui du visiteur, il partait en français.
        avis = self._avis_d_activite(t, anglo)
        self.assertTrue(avis, "aucun avis d'assignation pour le gestionnaire anglais")
        self.assertIn("assigned to you", avis[0].subject or "")

    def test_courriel_d_abus_dans_la_langue_du_bureau_et_des_destinataires(self):
        """Si ça casse : le bureau d'abus et les destinataires reçoivent le
        courriel en français en dur (ou dans la langue du signaleur)."""
        bureau = self._adresse("abus")
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_securetransfer.abuse_email", bureau)
        self._contact(bureau, "en_CA")
        dest_en, dest_fr = self._adresse("dest-en"), self._adresse("dest-fr")
        self._contact(dest_en, "en_CA")
        t = self._activer(self._transfert(
            locale="fr_CA", recipient_emails="%s, %s" % (dest_en, dest_fr)))
        avant = self.env["mail.mail"].sudo().search([])
        t.with_context(lang="fr_CA")._send_abuse_notice(
            reason="pourriel", ip="198.51.100.7")
        nouveaux = self.env["mail.mail"].sudo().search([]) - avant
        desk = nouveaux.filtered(lambda m: m.email_to == bureau)
        self.assertEqual(len(desk), 1)
        self.assertEqual(desk.subject,
                         "[Abuse] Transfer reported and suspended: %s" % t.name)
        self.assertIn("reported as abusive", desk.body_html)
        self.assertIn("Reporter&#39;s IP", desk.body_html)
        self.assertIn("Reactivate the transfer", desk.body_html)
        self.assertNotIn("Référence", desk.body_html)
        mail_en = nouveaux.filtered(lambda m: m.email_to == dest_en)
        self.assertEqual(mail_en.subject,
                         "Transfer suspended for verification: %s" % t.name)
        self.assertIn("Hello,", mail_en.body_html)
        self.assertNotIn("Bonjour", mail_en.body_html)
        mail_fr = nouveaux.filtered(lambda m: m.email_to == dest_fr)
        self.assertEqual(mail_fr.subject,
                         "Transfert suspendu pour vérification — %s" % t.name)

    def test_courriel_d_abus_sans_contact_dans_la_langue_de_la_societe(self):
        """Si ça casse : un bureau d'abus sans fiche reçoit l'alerte dans la
        langue du transfert (celle de l'expéditeur), pas dans celle de son
        opérateur."""
        bureau = self._adresse("abus-sans-fiche")
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_securetransfer.abuse_email", bureau)
        t = self._activer(self._transfert(locale="fr_CA"))
        (t.company_id or self.env.company).partner_id.lang = "en_CA"
        t.with_context(lang="fr_CA")._send_abuse_notice(reason="x", ip="198.51.100.7")
        desk = self._courriels(bureau)
        self.assertEqual(desk.subject,
                         "[Abuse] Transfer reported and suspended: %s" % t.name)

    # -------------------------------------------------- avis et codes de la page
    def test_avis_de_nouveau_visiteur_dans_la_langue_de_l_expediteur(self):
        """Si ça casse : l'expéditeur reçoit l'avis dans la langue du visiteur
        qui vient de confirmer son identité."""
        expediteur = self._adresse("exp-en")
        self._contact(expediteur, "en_CA")
        t = self._activer(self._transfert(
            locale="fr_CA", sender_email=expediteur, recipient_emails=""))
        t.write({"audience_mode": "open", "notify_on_join": True})
        membre = t._audience_join("email", self._adresse("visiteur"))
        self.assertTrue(membre)
        t.with_context(lang="fr_CA")._notify_audience_join(membre)
        avis = self._courriels(expediteur).filtered(
            lambda m: membre.display_identity in (m.body_html or ""))
        self.assertEqual(len(avis), 1)
        self.assertEqual(avis.subject, "New visitor: %s" % t.name)
        self.assertIn("A new person confirmed their identity", avis.body_html)

    def test_code_demande_depuis_la_page_par_un_inconnu(self):
        """Si ça casse : un destinataire sans fiche qui demande son code depuis
        une page anglaise le reçoit dans la langue de l'expéditeur."""
        inconnu = self._adresse("inconnu")
        t = self._activer(self._transfert(locale="fr_CA", recipient_emails=inconnu))
        t.force_recipient_otp = True
        otp_hash, _expiry = t.with_context(lang="en_CA")._send_recipient_otp(inconnu)
        self.assertTrue(otp_hash)
        mail = self._courriels(inconnu).filtered(
            lambda m: CODE_RE.search(m.body_html or ""))
        self.assertEqual(len(mail), 1)
        self.assertEqual(mail.subject, "Your confirmation code: %s" % t.name)
        self.assertIn("A sender shared files with you", mail.body_html)

    def test_code_d_audience_ouverte_demande_depuis_la_page(self):
        """Si ça casse : en audience ouverte, le visiteur (jamais une fiche)
        reçoit son code dans la langue de l'expéditeur, pas dans celle de sa page."""
        t = self._activer(self._transfert(locale="fr_CA", recipient_emails=""))
        t.write({"audience_mode": "open", "audience_allow_sms": False})
        visiteur = self._adresse("visiteur-ouvert")
        otp_hash, _expiry, membre = t.with_context(lang="en_CA")._send_audience_otp(
            "email", visiteur)
        self.assertTrue(otp_hash and membre)
        mail = self._courriels(visiteur)
        self.assertEqual(len(mail), 1)
        self.assertEqual(mail.subject, "Your confirmation code: %s" % t.name)

    # ------------------------------------------- notes du journal (langue du transfert)
    def test_notes_du_cycle_dans_la_langue_du_transfert(self):
        """Si ça casse : le journal (et le certificat qui le reprend) mêle la
        langue de chaque déclencheur : visiteur, cron, opérateur."""
        t = self._transfert(locale="en_CA", appelant="fr_CA")
        self.assertTrue(self._note(t, "created").startswith("Sender "))
        t = self._activer(t)
        self.assertTrue(self._note(t, "finalized").startswith(
            "1 file(s), 4096 byte(s), expires on "))
        self.assertTrue(any(n.startswith("Link sent to: ") for n in self._notes(t, "emailed")))
        t._notify_download()
        self.assertEqual(self._note(t, "notified"), "Download notice sent to the sender")
        t.message_post(body=Markup("<p>Lien : %s</p>") % t._share_url())
        self.assertTrue(t._redact_chatter_links(force=True))
        self.assertEqual(
            self._note(t, "link_redacted"),
            "Share link masked in the transfer log (no recipient code guards "
            "access to it).")
        t.max_downloads = 1
        t._register_download(t.file_ids[:1], "198.51.100.7", "agent-essai/1.0")
        self.assertEqual(t.state, "expired")
        self.assertEqual(self._note(t, "expired"), "Download budget exhausted")

    def test_notes_de_destruction_apres_lecture(self):
        t = self._activer(self._transfert(locale="en_CA"))
        t.burn_after_download = True
        t.with_context(lang="fr_CA")._register_download(
            t.file_ids[:1], "198.51.100.7", "agent-essai/1.0")
        self.assertEqual(self._note(t, "expired"), "Destroyed after reading (burn)")

    def test_notes_des_actions_d_operateur(self):
        """Si ça casse : un opérateur francophone écrit en français dans le
        journal d'un transfert anglais."""
        fr = {"lang": "fr_CA"}
        t1 = self._activer(self._transfert()).with_context(**fr)
        t1.action_suspend()
        self.assertEqual(self._note(t1, "suspended"), "Manual suspension (abuse)")
        t1.action_reactivate()
        self.assertEqual(self._note(t1, "reactivated"),
                         "Reactivated after review (report dismissed)")
        t1.action_expire_now()
        self.assertEqual(self._note(t1, "expired"), "Manual expiry")

        t2 = self._activer(self._transfert()).with_context(**fr)
        t2._suspend_for_abuse(ip="198.51.100.7")
        self.assertEqual(
            self._note(t2, "suspended"),
            "Automatically suspended following an abuse report, awaiting "
            "review by the administrator")

        t3 = self._activer(self._transfert()).with_context(**fr)
        t3.action_require_recipient_otp()
        self.assertEqual(
            self._note(t3, "otp_forced"),
            "Recipient code required after the fact; emails already sent are "
            "not recalled.")
        t3.extend_expiry(30)
        self.assertTrue(self._note(t3, "extended").startswith(
            "Expiry moved to 30 day(s): new expiry on "))

        t4 = self._activer(self._transfert()).with_context(**fr)
        with patch(S3_MOD + ".delete_keys", return_value=[]), \
                patch(S3_MOD + ".mpu_abort", return_value=None):
            t4.action_purge_now()
        self.assertIn("Manual expiry (immediate purge)", self._notes(t4, "expired"))
        self.assertEqual(self._note(t4, "purged"),
                         "1 object(s) deleted from storage; metadata and log kept")

        t5 = self._transfert().with_context(**fr)
        t5._register_file("brouillon.pdf", 4096)
        with patch(S3_MOD + ".delete_keys", return_value=[]), \
                patch(S3_MOD + ".mpu_abort", return_value=None):
            t5._purge_s3()
        self.assertEqual(self._note(t5, "purged"),
                         "Abandoned draft: 1 object(s) deleted from storage")

    def test_notes_du_code_de_l_expediteur(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_securetransfer.require_sender_otp", "1")
        t = self._transfert(locale="en_CA").with_context(lang="fr_CA")
        t._register_file("rapport.pdf", 4096)
        with patch(S3_MOD + ".head_object", side_effect=_tete(t)):
            t.action_finalize()
        self.assertEqual(self._note(t, "otp_sent"),
                         "Confirmation code sent to the sender")
        code = CODE_RE.search(self._courriels(t.sender_email)[-1].body_html).group(1)
        t.confirm_sender_otp(code)
        self.assertEqual(self._note(t, "otp_ok"), "Sender confirmed by code")

    def test_notes_de_l_audience(self):
        """Audience ouverte, puis destinataire nommé : demandes, codes,
        confirmations, blocage et déblocage d'un visiteur."""
        fr = {"lang": "fr_CA"}
        t = self._activer(self._transfert(recipient_emails="")).with_context(**fr)
        t.write({"audience_mode": "open", "audience_allow_sms": False,
                 "notify_on_join": False})
        otp_hash, _expiry, membre = t._send_audience_otp(
            "email", self._adresse("visiteur"))
        self.assertTrue(otp_hash)
        self.assertEqual(self._note(t, "audience_requested"),
                         "Self-declared identity on an open audience (email)")
        self.assertEqual(self._note(t, "otp_sent"),
                         "Code sent to an open audience visitor (email)")
        t._audience_confirm(membre)
        self.assertEqual(self._note(t, "audience_joined"),
                         "Visitor confirmed (open audience)")
        membre.with_context(**fr).action_block()
        self.assertEqual(self._note(t, "audience_blocked"),
                         "Visitor blocked: %s" % membre.display_identity)
        membre.with_context(**fr).action_unblock()
        self.assertEqual(self._note(t, "audience_unblocked"),
                         "Visitor unblocked: %s" % membre.display_identity)

        dest = self._adresse("dest-nomme")
        n = self._activer(self._transfert(recipient_emails=dest)).with_context(**fr)
        n.force_recipient_otp = True
        otp_hash, _expiry = n._send_recipient_otp(dest)
        self.assertTrue(otp_hash)
        self.assertEqual(self._note(n, "audience_requested"),
                         "Code requested by a named recipient")
        self.assertEqual(self._note(n, "otp_sent"),
                         "Confirmation code sent to the recipient (email)")
        n._audience_confirm(n._audience_for("email", dest))
        self.assertEqual(self._note(n, "audience_joined"),
                         "Visitor confirmed (named recipient)")

    def test_note_d_expiration_par_le_cron(self):
        t = self._activer(self._transfert())
        t.sudo().expiry_date = fields.Datetime.now() - timedelta(minutes=5)
        Transfer = type(self.env["secure.transfer"])
        with patch.object(Transfer, "_purge_s3", lambda self, actor=None: True):
            self.env["secure.transfer"].with_context(lang="fr_CA")._cron_purge_expired()
        self.assertEqual(self._note(t, "expired"), "Expiry date reached")

    def test_note_de_revelation_du_lien(self):
        gestionnaire = self._usager("gest-reveal", "fr_CA", gestionnaire=True)
        t = self._activer(self._transfert())
        Wizard = self.env["secure.transfer.reveal.wizard"].with_user(
            gestionnaire).with_context(lang="fr_CA", default_transfer_id=t.id)
        Wizard.create({}).action_confirm_reveal()
        self.assertEqual(self._note(t, "link_revealed"),
                         "Share link revealed from the backend.")

    # ----------------------------------------------------------- certificat
    def test_certificat_rendu_dans_la_langue_du_transfert(self):
        """Si ça casse : les libellés du certificat suivent qui l'imprime ; le
        client anglophone reçoit un certificat français (et l'inverse)."""
        Report = self.env["ir.actions.report"]
        anglais = self._activer(self._transfert(locale="en_CA"))
        html = Report.with_context(lang="fr_CA")._render_qweb_html(
            "bf_securetransfer.action_report_access_certificate",
            anglais.ids)[0].decode()
        self.assertIn("Certificate issued on", html)
        self.assertNotIn("Certificat produit le", html)
        francais = self._activer(self._transfert(locale="fr_CA", appelant="en_CA"))
        html = Report.with_context(lang="en_CA")._render_qweb_html(
            "bf_securetransfer.action_report_access_certificate",
            francais.ids)[0].decode()
        self.assertIn("Certificat produit le", html)
        self.assertNotIn("Certificate issued on", html)

    # ----------------------------------------------------------- export CSV
    def test_export_csv_dans_la_langue_de_l_operateur(self):
        """Si ça casse : l'en-tête et le nom du fichier exporté restent en
        français en dur pour un opérateur anglophone."""
        t = self._activer(self._transfert(locale="fr_CA"))
        for lang, nom, entete, chaine in (
                ("en_CA", "access-log-%s.csv", "# Access log: %s", "Integrity chain: INTACT"),
                ("fr_CA", "journal-acces-%s.csv", "# Journal d'accès : %s",
                 "Chaîne d'intégrité : INTACTE")):
            action = t.with_context(lang=lang).action_export_log()
            att_id = int(re.search(r"/web/content/(\d+)", action["url"]).group(1))
            att = self.env["ir.attachment"].browse(att_id)
            self.assertEqual(att.name, nom % t.name, lang)
            premiere = base64.b64decode(att.datas).decode("utf-8-sig").splitlines()[0]
            self.assertIn(entete % t.name, premiere, lang)
            self.assertIn(chaine, premiere, lang)

    # --------------------------------------------------- purge S3 en échec répété
    def test_activite_de_purge_en_echec_dans_la_langue_de_l_admin(self):
        """Si ça casse : l'admin anglophone reçoit l'alerte de purge (et son avis
        d'assignation) dans la langue d'OdooBot, qui fait tourner le cron."""
        admin = self.env.ref("base.user_admin")
        admin.lang = "en_CA"
        t = self._activer(self._transfert(locale="fr_CA"))
        t.sudo().purge_error_count = 4
        with patch(S3_MOD + ".delete_keys", return_value=["cle-restante"]), \
                patch(S3_MOD + ".mpu_abort", return_value=None):
            self.assertFalse(t.with_context(lang="fr_CA")._purge_s3())
        act = self.env["mail.activity"].sudo().search([
            ("res_model", "=", "secure.transfer"), ("res_id", "=", t.id),
            ("user_id", "=", admin.id)])
        self.assertEqual(act.summary, "S3 purge repeatedly failing: %s" % t.name)
        self.assertIn("5 purge attempts have failed", act.note)
        avis = self._avis_d_activite(t, admin)
        self.assertTrue(avis, "aucun avis d'assignation pour l'admin")
        self.assertIn("assigned to you", avis[0].subject or "")

    # ------------------------------------------------------------ sondes S3
    def test_verdicts_des_sondes_s3_traduits(self):
        """Si ça casse : la notification « Configuration du bucket » mêle des
        verdicts français en dur à un réglage anglais (ou l'inverse)."""
        client = MagicMock(name="s3_client")
        client.get_bucket_location.return_value = {"LocationConstraint": "us-west-1"}
        client.get_object_lock_configuration.return_value = {
            "ObjectLockConfiguration": {"ObjectLockEnabled": "Enabled"}}
        with patch.object(s3, "params", return_value={
                "bucket": "seau-essai", "region": "ca-east-1"}), \
                patch.object(s3, "client", return_value=client), \
                patch.object(s3, "key_prefix", return_value="transfers-essai"), \
                patch.object(s3, "apply_cors", return_value=[]), \
                patch.object(s3, "_http", side_effect=AssertionError("pas de réseau")):
            en = s3.setup_bucket(self.env(context={"lang": "en_CA"}))
            fr = s3.setup_bucket(self.env(context={"lang": "fr_CA"}))
        self.assertEqual(en["location"]["detail"],
                         "LocationConstraint='us-west-1' (expected 'ca-east-1')")
        self.assertEqual(fr["location"]["detail"],
                         "LocationConstraint='us-west-1' (attendu 'ca-east-1')")
        self.assertTrue(en["object_lock"]["detail"].startswith("object lock ENABLED"))
        self.assertTrue(fr["object_lock"]["detail"].startswith("object lock ACTIVÉ"))
        self.assertTrue(en["cors"]["detail"].startswith("no origin"))
        self.assertTrue(fr["cors"]["detail"].startswith("aucune origine"))

    # ------------------------------------------------------ page de dépôt d'usager
    def test_libelle_de_la_page_de_depot_garde_son_francais_exact(self):
        """Si ça casse : le catalogue ne rend plus EXACTEMENT l'ancien libellé
        « Envoi de fichiers | %s » et chaque page de dépôt française est renommée
        à la prochaine modification de son propriétaire (chaque synchro, sur
        un locataire qui en provisionne)."""
        self.assertEqual(
            self.env(context={"lang": "fr_CA"})._("File drop | %s", "Jeanne Exemple"),
            "Envoi de fichiers | Jeanne Exemple")
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_securetransfer.autoprovision_user_pages", "1")
        self.env.company.partner_id.lang = "fr_CA"
        # L'usager et l'appelant sont anglophones : la page reste celle de la
        # société, qui la publie à ses visiteurs.
        usager = self._usager("depot-fr", "en_CA").with_context(lang="en_CA")
        Brand = self.env["secure.transfer.brand"].sudo().with_context(active_test=False)
        page = Brand.search([("owner_user_id", "=", usager.id)])
        self.assertEqual(page.name, "Envoi de fichiers | %s" % usager.name)
        # Une synchro suivante (courriel changé) ne touche que le destinataire.
        nouvelle = self._adresse("depot-fr-nouvelle")
        usager.write({"email": nouvelle})
        self.assertEqual(page.fixed_recipient, nouvelle)
        self.assertEqual(page.name, "Envoi de fichiers | %s" % usager.name)

    def test_libelle_de_la_page_de_depot_dans_la_langue_de_la_societe(self):
        """Si ça casse : une société anglophone publie « Envoi de fichiers » en
        dur, ou le libellé suit l'appelant (un administrateur francophone)."""
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_securetransfer.autoprovision_user_pages", "1")
        self.env.company.partner_id.lang = "en_CA"
        usager = self.env["res.users"].with_context(
            lang="fr_CA", no_reset_password=True).create({
                "name": "Jane Doe Essai", "login": self._adresse("depot-en"),
                "email": self._adresse("depot-en-courriel"), "lang": "fr_CA",
                "groups_id": [(6, 0, [self.env.ref("base.group_user").id])]})
        page = self.env["secure.transfer.brand"].sudo().with_context(
            active_test=False).search([("owner_user_id", "=", usager.id)])
        self.assertEqual(page.name, "File drop | Jane Doe Essai")

    # ------------------------------------------------------- tailles de fichiers
    def _corps_anglais_seul(self, xmlid):
        """Le corps anglais livré, seul en base (clé en_US) : la forme d'une base
        installée ou montée depuis la source anglaise ; tout lecteur y retombe."""
        gabarit = self.env.ref(xmlid).sudo()
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE mail_template SET body_html = jsonb_build_object('en_US', %s::text)"
            " WHERE id = %s", [_livre(gabarit, "body_html"), gabarit.id])
        self.env.invalidate_all()
        return gabarit

    def test_taille_avec_unite_anglaise_pour_un_lecteur_anglais(self):
        """Si ça casse : un lecteur anglais lit « 2 Go in total » ou « 4 Ko » :
        l'unité était figée en français dans l'expression QWeb (jamais traduite)."""
        en = self.env(context={"lang": "en_CA"})
        self.assertEqual(size_label(en, 500), "500 B")
        self.assertEqual(size_label(en, 4096), "4 KB")
        self.assertEqual(size_label(en, 5 * MB), "5.0 MB")
        self.assertEqual(size_label(en, 3 * GB), "3.00 GB")
        self.assertEqual(size_label(en, 2 * GB, limit=True), "2 GB")
        self.assertEqual(size_label(en, 500 * MB, limit=True), "500 MB")
        t = self._transfert(locale="en_CA")
        t._register_file("rapport.pdf", 4096)
        self.assertEqual(t.file_ids.with_context(lang="en_CA")._size_label(), "4 KB")

    def test_taille_avec_unite_francaise_par_le_catalogue(self):
        """Si ça casse : le lecteur francophone perd « Go », « Mo », « Ko », « o »
        (le catalogue ne traduit plus les unités anglaises de la source)."""
        fr = self.env(context={"lang": "fr_CA"})
        self.assertEqual(size_label(fr, 500), "500 o")
        self.assertEqual(size_label(fr, 4096), "4 Ko")
        self.assertEqual(size_label(fr, 5 * MB), "5.0 Mo")
        self.assertEqual(size_label(fr, 3 * GB), "3.00 Go")
        self.assertEqual(size_label(fr, 2 * GB, limit=True), "2 Go")
        # Un lecteur francophone qui retombe sur le corps anglais (aucune clé
        # fr_CA) lit au moins des unités françaises.
        t = self._activer(self._transfert(locale="fr_CA"))
        gabarit = self._corps_anglais_seul("bf_securetransfer.mail_template_transfer_link")
        corps = gabarit.with_context(lang="fr_CA")._render_field("body_html", t.ids)[t.id]
        self.assertIn("4 Ko", corps)

    def test_taille_des_fichiers_dans_les_courriels_du_lecteur_anglais(self):
        """Si ça casse : le lien de téléchargement et l'accusé de réception d'un
        lecteur anglais listent les fichiers en « Ko » (expression du corps)."""
        t = self._activer(self._transfert(locale="en_CA"))
        for xmlid in ("bf_securetransfer.mail_template_transfer_link",
                      "bf_securetransfer.mail_template_transfer_receipt"):
            gabarit = self._corps_anglais_seul(xmlid)
            self.assertIn("f._size_label()", str(gabarit.body_html), xmlid)
            corps = gabarit.with_context(lang="en_CA")._render_field(
                "body_html", t.ids)[t.id]
            self.assertIn("rapport.pdf", corps, xmlid)
            self.assertIn("4 KB", corps, xmlid)
            self.assertNotRegex(str(corps), UNITE_FR_RE, xmlid)


@tagged("post_install", "-at_install")
class TestMigrationCorpsLivres(TransactionCase):
    """Migration 18.0.1.22.0 : un corps de courriel livré passe en anglais en
    en_US et garde (ou reçoit) le français en fr_CA ; une retouche est laissée."""

    XMLID = "bf_securetransfer.mail_template_download_notice"
    CLE = ("data/secure_transfer_mail_template.xml",
           "mail_template_download_notice", "body_html")
    CLE_OBJET = ("data/secure_transfer_mail_template.xml",
                 "mail_template_download_notice", "subject")

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for code in ("fr_CA", "en_CA"):
            cls.env["res.lang"]._activate_lang(code)

    @staticmethod
    def _script(nom):
        chemin = os.path.join(MODULE_DIR, "migrations", "18.0.1.22.0", nom)
        spec = importlib.util.spec_from_file_location(
            "bf_st_mig_" + nom.replace("-", "_").replace(".py", ""), chemin)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _poser(self, gabarit, champ, valeurs):
        """La valeur brute d'une base d'avant la montée (jsonb par langue)."""
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE mail_template SET %s = %%s::jsonb WHERE id = %%s" % champ,
            [json.dumps(valeurs), gabarit.id])
        self.env.invalidate_all()

    def _monter(self):
        self._script("post-migrate.py").migrate(self.env.cr, "18.0.1.21.9")
        self._script("end-migrate.py").migrate(self.env.cr, "18.0.1.21.9")
        self.env.invalidate_all()

    def test_corps_livre_passe_en_anglais_et_garde_le_francais(self):
        """Si ça casse : en_US garde le corps français livré (un usager anglais
        reçoit l'avis en français), ou fr_CA reçoit l'anglais."""
        gabarit = self.env.ref(self.XMLID)
        livres = self._script("end-migrate.py").LIVRES_FR
        # Odoo range un champ HTML ASSAINI : c'est la forme d'une base d'avant.
        stocke = str(gabarit._fields["body_html"].convert_to_column(
            livres[self.CLE], gabarit)).strip()
        self._poser(gabarit, "body_html", {"en_US": stocke})
        self._poser(gabarit, "subject", {"en_US": livres[self.CLE_OBJET]})
        self._monter()
        en = gabarit.with_context(lang="en_US")
        fr = gabarit.with_context(lang="fr_CA")
        self.assertIn("have just been downloaded", en.body_html)
        self.assertNotIn("viennent d", en.body_html)
        self.assertIn("viennent d", fr.body_html)
        self.assertNotIn("have just been downloaded", fr.body_html)
        self.assertEqual(en.subject, "Your files were downloaded: {{ object.name }}")
        self.assertEqual(fr.subject, livres[self.CLE_OBJET])

    def test_ancienne_forme_livree_passe_en_anglais(self):
        """Un corps livré par une version passée (anthracite #2D3031, vu sur la démo) est le
        livré, pas une retouche : il passe à l'anglais. Si ça casse : un lecteur anglais de
        cette base reçoit l'avis en français."""
        gabarit = self.env.ref(self.XMLID)
        livres = self._script("end-migrate.py").LIVRES_FR
        stocke = str(gabarit._fields["body_html"].convert_to_column(
            livres[self.CLE], gabarit)).strip()
        ancien = stocke.replace("#2E3132", "#2D3031")
        self.assertNotEqual(ancien, stocke)
        self._poser(gabarit, "body_html", {"en_US": ancien})
        self._monter()
        en = gabarit.with_context(lang="en_US")
        self.assertIn("have just been downloaded", en.body_html)
        self.assertNotIn("viennent d", en.body_html)

    def test_sortie_d_un_ancien_crochet_passe_a_l_anglais_livre(self):
        """Une clé en_CA ÉGALE à en_US est la sortie d'un ancien crochet (qui
        recopiait la source dans en_CA), pas une retouche : en_US et en_CA passent
        à l'anglais livré, fr_CA reçoit le français du catalogue. Si ça casse : la
        montée la laisse comme une retouche, et chaque lecteur garde l'ancienne
        sortie (objet « — » compris) ; ou fr_CA reste sans français."""
        gabarit = self.env.ref(self.XMLID)
        livres = self._script("end-migrate.py").LIVRES_FR
        ancien = _livre(gabarit, "body_html").replace(
            "have just been downloaded", "were just downloaded")
        self.assertIn("were just downloaded", ancien)
        ancien_objet = "Your files were downloaded — {{ object.name }}"
        self._poser(gabarit, "body_html", {"en_US": ancien, "en_CA": ancien})
        self._poser(gabarit, "subject", {"en_US": ancien_objet, "en_CA": ancien_objet})
        self._monter()
        for lang in ("en_US", "en_CA"):
            g = gabarit.with_context(lang=lang)
            self.assertIn("have just been downloaded", g.body_html, lang)
            self.assertNotIn("were just downloaded", g.body_html, lang)
            self.assertEqual(g.subject, _livre(gabarit, "subject"), lang)
        fr = gabarit.with_context(lang="fr_CA")
        self.assertIn("viennent d", fr.body_html)
        self.assertEqual(fr.subject, livres[self.CLE_OBJET])
        # Contre-épreuve : la même valeur sans copie en en_CA est une retouche.
        self._poser(gabarit, "body_html", {"en_US": ancien, "en_CA": "<p>Autre</p>"})
        self._monter()
        self.assertIn("were just downloaded",
                      gabarit.with_context(lang="en_US").body_html)

    def test_corps_retouche_est_laisse(self):
        """Si ça casse : la montée écrase un corps retouché par le client, ou le
        français livré du catalogue le masque en fr_CA."""
        gabarit = self.env.ref(self.XMLID)
        retouche = "<p>Corps retouché par l'opérateur</p>"
        self._poser(gabarit, "body_html", {"en_US": retouche})
        self._monter()
        self.assertEqual(str(gabarit.with_context(lang="en_US").body_html).strip(),
                         retouche)
        self.assertEqual(str(gabarit.with_context(lang="fr_CA").body_html).strip(),
                         retouche)


@tagged("post_install", "-at_install")
class TestPagesPubliquesLangue(_Outils, HttpCase):
    """Les routes publiques réelles : la langue vient de l'en-tête du visiteur."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _preparer(cls)

    def setUp(self):
        super().setUp()
        for limiter in _LIMITERS:
            with limiter._lock:
                limiter._data.clear()
        p = patch(MAIL_SEND, lambda self, *a, **k: True)
        p.start()
        self.addCleanup(p.stop)

    def _get(self, path, langue):
        res = self.url_open(path, headers={"Accept-Language": langue},
                            allow_redirects=False)
        self.env.invalidate_all()
        return res

    def _post(self, path, langue, data):
        res = self.url_open(path, data=data, headers={"Accept-Language": langue},
                            allow_redirects=False)
        self.env.invalidate_all()
        return res

    def test_page_d_envoi_anglaise_quand_seule_en_us_est_active(self):
        """Si ça casse : sur une base sans en_CA, le visiteur anglophone reçoit
        la page d'envoi en français (titre, chaînes du script, unités)."""
        _sans_en_ca(self.env)
        res = self._get("/secrets", "en-US,en;q=0.9")
        self.assertEqual(res.status_code, 200, res.text[:300])
        self.assertIn("<title>Send files —", res.text)
        self.assertIn('"locale": "en_CA"', res.text)
        self.assertIn('"KB"', res.text)
        self.assertIn('data-template="Expires on {date} (UTC)."', res.text)
        self.assertNotIn("Envoyer des fichiers", res.text)
        # La limite d'envoi (« Up to N files, 2 GB in total. »).
        self.assertRegex(res.text, r"\d+(\.\d+)? (GB|MB)\s+in total\.")
        self.assertNotRegex(res.text, r"\d (Go|Mo)\b")

    def test_page_d_envoi_francaise(self):
        res = self._get("/secrets", "fr-CA,fr;q=0.9")
        self.assertEqual(res.status_code, 200, res.text[:300])
        self.assertIn("<title>Envoyer des fichiers —", res.text)
        self.assertIn('"locale": "fr_CA"', res.text)
        self.assertIn('data-template="Expire le {date} (UTC)."', res.text)

    def test_page_de_telechargement_anglaise_quand_seule_en_us_est_active(self):
        """Si ça casse : le visiteur anglophone d'un transfert français lit la
        page, et la date d'expiration, dans la langue de l'expéditeur."""
        t = self._activer(self._transfert(locale="fr_CA"))
        # Midi UTC : la même date dans tous les fuseaux du visiteur.
        t.sudo().expiry_date = fields.Datetime.now().replace(
            hour=12, minute=0, second=0, microsecond=0) + timedelta(days=5)
        _sans_en_ca(self.env)
        attendu = format_date(self.env, t.expiry_date, lang_code="en_US")
        self.assertNotEqual(attendu, format_date(self.env, t.expiry_date,
                                                 lang_code="fr_CA"))
        res = self._get("/s/%s" % t.sudo().token, "en-US")
        self.assertEqual(res.status_code, 200, res.text[:300])
        self.assertIn("<title>Shared files —", res.text)
        self.assertIn('data-confirm="Report this transfer as abusive?"', res.text)
        self.assertIn(attendu, res.text)
        self.assertNotIn("Fichiers partagés", res.text)
        # La taille du fichier, avec l'unité du visiteur.
        self.assertIn('class="st-file-size">4 KB<', res.text)
        self.assertNotRegex(res.text, UNITE_FR_RE)

    def test_erreurs_json_dans_la_langue_du_visiteur(self):
        """Si ça casse : la page est anglaise mais ses erreurs sortent dans la
        langue que la session portait déjà (ici, le français)."""
        for session, langue, attendu in (
                ("fr_CA", "en-CA", "Invalid or expired upload link."),
                ("en_CA", "fr-CA", "Lien de téléversement invalide ou expiré.")):
            self.authenticate(None, None, session_extra={"context": {"lang": session}})
            res = self.opener.post(
                self.base_url() + "/secrets/api/jeton-inexistant-essai/presign",
                json={"jsonrpc": "2.0", "method": "call",
                      "params": {"filename": "a.pdf", "size": 10}, "id": 1},
                headers={"Accept-Language": langue}, timeout=30)
            self.assertEqual(res.status_code, 200)
            resultat = res.json()["result"]
            self.assertEqual(resultat["error"], "not_found")
            self.assertEqual(resultat["message"], attendu, langue)

    def test_code_et_confirmation_depuis_la_page(self):
        """Si ça casse : le destinataire sans fiche reçoit son code dans la
        langue de l'expéditeur, ou la note de confirmation suit le visiteur."""
        dest = self._adresse("dest-page")
        t = self._activer(self._transfert(locale="en_CA", recipient_emails=dest))
        t.force_recipient_otp = True
        res = self._post("/s/%s/otp-request" % t.sudo().token, "fr-CA",
                         {"email": dest})
        self.assertEqual(res.status_code, 303, res.text[:300])
        mail = self._courriels(dest).filtered(
            lambda m: CODE_RE.search(m.body_html or ""))
        self.assertEqual(len(mail), 1)
        self.assertEqual(mail.subject, "Votre code de confirmation — %s" % t.name)
        code = CODE_RE.search(mail.body_html).group(1)
        res = self._post("/s/%s/otp-verify" % t.sudo().token, "fr-CA", {"code": code})
        self.assertEqual(res.status_code, 303, res.text[:300])
        self.assertEqual(self._note(t, "otp_ok"), "Recipient confirmed by code")

    def test_signalement_non_verifie(self):
        """Si ça casse : la note du signalement suit le visiteur francophone sur
        un transfert anglais, et le gestionnaire anglophone reçoit une activité
        française."""
        anglo = self._usager("gest-http-en", "en_CA", gestionnaire=True)
        t = self._activer(self._transfert(locale="en_CA"), password="secret-essai-123")
        res = self._post("/s/%s/report" % t.sudo().token, "fr-CA",
                         {"reason": "pourriel"})
        self.assertEqual(res.status_code, 303, res.text[:300])
        self.assertEqual(self._note(t, "abuse_report"),
                         "[unverified, to review] pourriel")
        self.assertEqual(t.state, "active", "non vérifié : pas de suspension")
        act = self.env["mail.activity"].sudo().search([
            ("res_model", "=", "secure.transfer"), ("res_id", "=", t.id),
            ("user_id", "=", anglo.id)])
        self.assertEqual(act.summary, "Abuse report: %s" % t.name)
        self.assertIn("[unverified, to review]", act.note)
