"""E-mails et i18n : un courriel par destinataire, aucune fuite de mot de
passe, lien sur le domaine de marque, langue par contact, gabarits rendus
dans les deux langues, parité des chaînes d'interface, et hooks .po.

Aucun réseau : le passage S3 (``models.s3``) est patché, et
``MailMail.send`` est neutralisé — la suite ne doit jamais ouvrir de SMTP.

Piège de test respecté ici : pas de ``assertRaises`` autour d'un appel qui
écrit en base. L'``assertRaises`` d'Odoo annule son bloc dans un savepoint
``flush=False`` et emporte les enregistrements créés par le test ; on utilise
``try/except`` + ``self.fail()`` sur le chemin de succès.
"""
import ast
import os
import tempfile
import types


from unittest.mock import patch

from odoo.api import Environment
from odoo.tests import TransactionCase, tagged

from odoo.addons.bf_securetransfer import hooks
from odoo.addons.bf_securetransfer.controllers import main as st_main

S3_MOD = "odoo.addons.bf_securetransfer.models.s3"
MAIN_MOD = "odoo.addons.bf_securetransfer.controllers.main"
MODULE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MB = 1024 * 1024

_TEMPLATE_XIDS = (
    "bf_securetransfer.mail_template_transfer_link",
    "bf_securetransfer.mail_template_secure_message",
    "bf_securetransfer.mail_template_transfer_receipt",
    "bf_securetransfer.mail_template_download_notice",
)


@tagged("post_install", "-at_install")
class TestEmailsI18n(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.brand = cls.env.ref("bf_securetransfer.brand_default")
        icp = cls.env["ir.config_parameter"].sudo()
        # Roomy daily quotas so the suite never trips anti-abuse counters.
        icp.set_param("bf_securetransfer.quota_daily_transfers_per_ip", "500")
        icp.set_param("bf_securetransfer.quota_daily_transfers_per_sender", "500")
        icp.set_param("bf_securetransfer.quota_daily_bytes_per_ip_mb", "1000000")
        # No instance-wide OTP / allowlist: these tests exercise the plain
        # link path unless a test says otherwise.
        icp.set_param("bf_securetransfer.require_recipient_otp", "0")
        icp.set_param("bf_securetransfer.require_sender_otp", "0")
        icp.set_param("bf_securetransfer.default_sender_allowlist", "")
        icp.set_param("bf_securetransfer.default_recipient_allowlist", "")
        # en_CA must exist for the per-contact language paths.
        cls.env["res.lang"].sudo()._activate_lang("en_CA")

    def setUp(self):
        super().setUp()
        # Absolutely no SMTP: mail.mail records are created and inspected,
        # never delivered (and never auto-deleted by a successful send).
        p = patch("odoo.addons.mail.models.mail_mail.MailMail.send",
                  lambda self, *a, **k: True)
        p.start()
        self.addCleanup(p.stop)
        self.Mail = self.env["mail.mail"].sudo()
        self._mails_before = self.Mail.search([])

    # ------------------------------------------------------------------ fixtures
    def _create(self, **overrides):
        vals = {
            "sender_name": "Test Sender",
            "sender_email": "sender@example.com",
            "recipient_emails": "dest@example.com",
            "message": "Bonjour",
            "retention_days": 7,
        }
        brand = overrides.pop("brand", self.brand)
        vals.update(overrides)
        return self.env["secure.transfer"].api_create(
            brand, vals, "203.0.113.10", "test-suite/1.0", "fr_CA",
        )

    def _head_for(self, transfer):
        """head_object stub returning the declared size for any of the
        transfer's keys (finalize verification passes)."""
        sizes = {f.s3_key: int(f.size) for f in transfer.file_ids}

        def _head(env, key):
            if key in sizes:
                return {"size": sizes[key], "etag": "etag-" + key[-8:]}
            return None
        return _head

    def _finalize_with_file(self, transfer, filename="rapport.pdf", size=4096,
                            password=None):
        transfer._register_file(filename, size)
        with patch(S3_MOD + ".head_object", side_effect=self._head_for(transfer)):
            return transfer.action_finalize(password=password)

    def _new_mails(self):
        return self.Mail.search([]) - self._mails_before

    def _visuals_patch(self, **overrides):
        visuals = {
            "name": "Marque Test",
            "primary": "#29ABE1",
            "dark": "#2D3031",
            "logo_url": "",
            "logo_host": "",
            "favicon_url": "",
            "powered_by": False,
            "powered_by_name": "Opérateur",
            "tagline": "",
            "footer_html": "",
        }
        visuals.update(overrides)
        return patch.object(type(self.brand), "_visuals", lambda s: visuals)

    # ------------------------------------------------------- 1. un courriel / destinataire
    def test_one_mail_per_recipient_no_shared_to(self):
        """Si ça casse : les destinataires se voient mutuellement dans le
        champ To: — divulgation d'une liste de contacts entre organisations."""
        t = self._create(
            recipient_emails="a@example.com, b@example.com, c@example.com")
        self._finalize_with_file(t)

        recipients = {"a@example.com", "b@example.com", "c@example.com"}
        to_recipients = self._new_mails().filtered(
            lambda m: (m.email_to or "").strip() in recipients)
        self.assertEqual(
            len(to_recipients), 3,
            "il faut exactement un mail.mail par destinataire, obtenu : %s"
            % to_recipients.mapped("email_to"))
        self.assertEqual(
            sorted(m.email_to.strip() for m in to_recipients), sorted(recipients))
        for m in to_recipients:
            self.assertNotIn(
                ",", m.email_to or "",
                "un email_to multi-adresses expose la liste des destinataires")
            self.assertNotIn(",", m.email_cc or "")
            # Aucune autre adresse de la liste ne doit apparaître dans l'entête.
            others = recipients - {m.email_to.strip()}
            for other in others:
                self.assertNotIn(other, m.email_to or "")

    # -------------------------------------------------------------- 2. accusé expéditeur
    def test_receipt_goes_to_sender_only(self):
        """Si ça casse : l'expéditeur ne reçoit plus la preuve d'envoi ni le
        lien de partage — il n'a aucun moyen de retrouver son transfert."""
        t = self._create(recipient_emails="dest@example.com")
        self._finalize_with_file(t)

        receipts = self._new_mails().filtered(
            lambda m: (m.email_to or "").strip() == "sender@example.com")
        self.assertEqual(len(receipts), 1,
                         "un seul accusé doit partir vers l'expéditeur")
        receipt = receipts
        self.assertIn(t.name, receipt.subject or "")
        # Marqueurs propres au gabarit mail_template_transfer_receipt.
        self.assertIn("Transfert envoyé", receipt.body_html or "")
        self.assertIn("Consulter le transfert", receipt.body_html or "")
        # …et absents du gabarit destinataire, pour prouver qu'ils diffèrent.
        to_recipient = self._new_mails().filtered(
            lambda m: (m.email_to or "").strip() == "dest@example.com")
        self.assertEqual(len(to_recipient), 1)
        self.assertNotIn("Consulter le transfert", to_recipient.body_html or "")
        self.assertIn("Télécharger les fichiers", to_recipient.body_html or "")

    # ------------------------------------------------------------------ 3. mode lien seul
    def test_link_only_mode_sends_receipt_only(self):
        """Si ça casse : un transfert sans destinataire enverrait un courriel
        à une adresse vide (rejet SMTP) ou n'en enverrait aucun à l'expéditeur,
        qui perdrait le lien."""
        t = self._create(recipient_emails="")
        self.assertFalse(t.recipient_emails)
        self._finalize_with_file(t.with_context(lang="fr_CA"))

        new = self._new_mails()
        self.assertEqual(len(new), 1,
                         "mode lien seul : un seul courriel (l'accusé), obtenu %s"
                         % new.mapped("email_to"))
        self.assertEqual((new.email_to or "").strip(), "sender@example.com")

        emailed = t.access_log_ids.filtered(lambda e: e.action == "emailed")
        self.assertEqual(len(emailed), 1)
        self.assertIn("(aucun — mode lien seul)", emailed.note or "")
        self.assertIn("sender@example.com", emailed.note or "")

    # ------------------------------------------------------------- 4. mot de passe jamais
    def test_password_never_appears_in_any_email(self):
        """Si ça casse : le mot de passe voyage dans le même canal que le
        lien — la protection par mot de passe ne protège plus rien."""
        secret = "Chaperon-Rouge-88-XYZ"
        t = self._create(recipient_emails="dest@example.com")
        self._finalize_with_file(t, password=secret)
        self.assertTrue(t.has_password)
        self.assertEqual(t.state, "active")

        new = self._new_mails()
        self.assertEqual(len(new), 2, "lien + accusé attendus")
        for m in new:
            self.assertNotIn(secret, m.body_html or "",
                             "mot de passe en clair dans le corps de %s" % m.email_to)
            self.assertNotIn(secret, m.subject or "",
                             "mot de passe en clair dans l'objet de %s" % m.email_to)
            self.assertNotIn(secret.lower(), (m.body_html or "").lower())
        # L'empreinte non plus (elle permettrait une attaque hors ligne).
        pwd_hash = t.sudo().password_hash
        self.assertTrue(pwd_hash)
        for m in new:
            self.assertNotIn(pwd_hash, m.body_html or "")
        # Le destinataire est bien AVERTI qu'un mot de passe existe.
        to_recipient = new.filtered(
            lambda m: (m.email_to or "").strip() == "dest@example.com")
        self.assertIn("mot de passe", to_recipient.body_html or "")

    # --------------------------------------------------------- 5. lien = domaine de marque
    def test_share_link_uses_brand_domain_not_base_url(self):
        """Si ça casse : le lien pointe vers le domaine du back-office (sur
        projets.example.com) — page inaccessible au destinataire et
        fuite du domaine interne."""
        brand = self.env["secure.transfer.brand"].create({
            "name": "Marque domaine i18n",
            "domain": "secrets.i18n-test.example",
            "company_id": self.brand.company_id.id,
        })
        t = self._create(brand=brand, recipient_emails="dest@example.com")
        self._finalize_with_file(t)

        expected = "https://secrets.i18n-test.example/s/" + t.sudo().token
        new = self._new_mails()
        self.assertEqual(len(new), 2)
        for m in new:
            self.assertIn(expected, m.body_html or "",
                          "le lien de marque manque dans %s" % m.email_to)
        base_url = (self.env["ir.config_parameter"].sudo()
                    .get_param("web.base.url", "") or "").rstrip("/")
        if base_url and "secrets.i18n-test.example" not in base_url:
            for m in new:
                self.assertNotIn(
                    base_url + "/s/", m.body_html or "",
                    "web.base.url a fui dans le lien de partage")

    # --------------------------------------------------- 6. gabarit manquant → dégradation
    def test_missing_templates_degrade_without_exception(self):
        """Si ça casse : un opérateur qui supprime un gabarit fait planter
        chaque finalisation — plus aucun transfert ne peut être envoyé."""
        t = self._create(recipient_emails="dest@example.com")
        t._register_file("rapport.pdf", 4096)
        with patch(S3_MOD + ".head_object", side_effect=self._head_for(t)):
            t.action_finalize()
        self.assertEqual(t.state, "active")

        real_ref = Environment.ref

        def _ref(env_self, xml_id, raise_if_not_found=True):
            if xml_id in _TEMPLATE_XIDS:
                if raise_if_not_found:
                    raise ValueError(xml_id)
                return False
            return real_ref(env_self, xml_id,
                            raise_if_not_found=raise_if_not_found)

        logs_before = len(t.access_log_ids)
        with patch.object(Environment, "ref", _ref):
            try:
                t._send_link_emails()
                t._notify_download()
            except Exception as exc:  # noqa: BLE001 — c'est précisément le point
                self.fail("gabarit absent : le module doit dégrader, pas lever "
                          "(%s: %s)" % (type(exc).__name__, exc))
        # Le journal continue de tracer, même sans gabarit.
        self.assertGreater(len(t.access_log_ids), logs_before)
        self.assertIn("emailed", t.access_log_ids.mapped("action"))

    # ------------------------------------------------------------------ 7. _lang_for_email
    def test_lang_for_email_resolution(self):
        """Si ça casse : un contact anglophone reçoit un courriel français (ou
        l'inverse) — le multilingue promis au client ne fonctionne plus."""
        t = self._create()
        partner_en = self.env["res.partner"].create({
            "name": "English Contact",
            "email": "en-contact@i18n-test.example",
            "lang": "en_CA",
        })
        # a) contact Odoo connu avec une langue → sa langue
        self.assertEqual(
            t._lang_for_email("en-contact@i18n-test.example"), "en_CA")
        # …et la résolution est insensible à la casse (=ilike)
        self.assertEqual(
            t._lang_for_email("EN-Contact@i18n-test.example"), "en_CA")
        # b) contact inconnu → locale du transfert
        self.assertEqual(t.locale, "fr_CA")
        self.assertEqual(
            t._lang_for_email("inconnu@i18n-test.example"), "fr_CA")
        # c) contact connu SANS langue → repli sur la locale du transfert
        partner_en.sudo().write({"lang": False})
        self.assertEqual(
            t._lang_for_email("en-contact@i18n-test.example"), "fr_CA")
        # d) adresse vide → locale du transfert
        self.assertEqual(t._lang_for_email(""), "fr_CA")
        self.assertEqual(t._lang_for_email(False), "fr_CA")
        # e) locale vide → repli dur fr_CA (record en mémoire : le champ est
        #    requis en base, il ne peut être vidé que hors flush)
        virtual = self.env["secure.transfer"].new({
            "brand_id": self.brand.id, "locale": False,
        })
        self.assertFalse(virtual.locale)
        self.assertEqual(virtual._lang_for_email("inconnu@i18n-test.example"),
                         "fr_CA")

    def test_recipient_language_drives_its_own_email(self):
        """Si ça casse : tous les destinataires reçoivent la même langue, celle
        du transfert — l'anglophone reçoit du français."""
        self.env["res.partner"].create({
            "name": "English Recipient",
            "email": "en-dest@i18n-test.example",
            "lang": "en_CA",
        })
        t = self._create(
            recipient_emails="en-dest@i18n-test.example, fr-dest@i18n-test.example")
        self._finalize_with_file(t)
        new = self._new_mails()
        en_mail = new.filtered(
            lambda m: (m.email_to or "").strip() == "en-dest@i18n-test.example")
        fr_mail = new.filtered(
            lambda m: (m.email_to or "").strip() == "fr-dest@i18n-test.example")
        self.assertEqual(len(en_mail), 1)
        self.assertEqual(len(fr_mail), 1)
        # Le gabarit français est la source : le destinataire francophone doit
        # le recevoir tel quel.
        self.assertIn("Télécharger les fichiers", fr_mail.body_html or "")

    # ------------------------------------------------------------------ 8. _brand_email_from
    def test_brand_email_from_cascade(self):
        """Si ça casse : les courriels partent d'une adresse non déléguée →
        SPF/DKIM échouent et tout le transfert atterrit en pourriel."""
        brand = self.env["secure.transfer.brand"].create({
            "name": "Marque expéditeur",
            "company_id": self.brand.company_id.id,
            "email_from": "marque@i18n-test.example",
        })
        t = self._create(brand=brand)
        # 1) brand.email_from gagne
        self.assertEqual(t._brand_email_from(), "marque@i18n-test.example")
        # 2) sans marque → company.email_formatted
        brand.email_from = False
        company = t.company_id
        company.sudo().partner_id.write({"email": "compagnie@i18n-test.example"})
        self.env.flush_all()
        self.env.invalidate_all()
        self.assertTrue(company.email_formatted)
        self.assertEqual(t._brand_email_from(), company.email_formatted)
        self.assertIn("compagnie@i18n-test.example", t._brand_email_from())
        # 2b) sans courriel de société, res.company.email_formatted retombe
        #     sur le catchall du domaine d'alias (comportement Odoo/mail) : en
        #     production c'est CE palier, et non l'utilisateur, qui prend le
        #     relais tant qu'un domaine d'alias est configuré.
        company.sudo().partner_id.write({"email": False})
        self.env.flush_all()
        self.env.invalidate_all()
        if company.alias_domain_id:
            self.assertEqual(t._brand_email_from(), company.catchall_formatted)
        # 3) ni marque, ni société (ni catchall) → user.email_formatted
        company.sudo().write({"alias_domain_id": False})
        self.env.user.sudo().partner_id.write({"email": "operateur@i18n-test.example"})
        self.env.flush_all()
        self.env.invalidate_all()
        self.assertFalse(company.email_formatted)
        self.assertEqual(t._brand_email_from(), self.env.user.email_formatted)
        self.assertIn("operateur@i18n-test.example", t._brand_email_from())

    # ------------------------------------------------------------------ 9. _brand_email_shell
    def test_shell_prefixes_relative_logo_with_brand_base(self):
        """Si ça casse : le logo est une URL relative dans un courriel — aucun
        client de messagerie ne peut la résoudre, l'entête est vide."""
        brand = self.env["secure.transfer.brand"].create({
            "name": "Marque logo",
            "domain": "logo.i18n-test.example",
            "company_id": self.brand.company_id.id,
        })
        t = self._create(brand=brand)
        with self._visuals_patch(logo_url="/web/image/4242"):
            html = t._brand_email_shell("Titre", "<p>corps</p>")
        self.assertIn('src="https://logo.i18n-test.example/web/image/4242"', html)
        self.assertNotIn('src="/web/image/4242"', html)
        self.assertIn("<p>corps</p>", html)
        self.assertIn("Titre", html)

    def test_shell_keeps_absolute_logo_untouched(self):
        """Si ça casse : une URL absolue est re-préfixée
        (https://marque/https://cdn/...) et le logo casse."""
        t = self._create()
        with self._visuals_patch(logo_url="https://cdn.i18n-test.example/l.png"):
            html = t._brand_email_shell("Titre", "<p>x</p>")
        self.assertIn('src="https://cdn.i18n-test.example/l.png"', html)
        self.assertNotIn("/https://cdn.i18n-test.example", html)

    def test_shell_falls_back_to_text_name_without_logo(self):
        """Si ça casse : une marque sans logo envoie un courriel dont l'entête
        est un <img> vide — le destinataire ne voit plus de qui ça vient."""
        t = self._create()
        with self._visuals_patch(logo_url="", name="Marque Sans Logo"):
            html = t._brand_email_shell("Titre", "<p>x</p>")
        self.assertNotIn("<img", html)
        self.assertIn(">Marque Sans Logo<", html)

    def test_shell_escapes_brand_fields(self):
        """Si ça casse : un champ de marque (couleur, nom) devient un vecteur
        d'injection HTML dans tous les courriels de cette marque."""
        payload = '#fff"><script>alert(1)</script>'
        t = self._create()
        with self._visuals_patch(primary=payload, dark=payload,
                                 name='<img src=x onerror=alert(2)>'):
            html = t._brand_email_shell('<b>Titre</b>', "<p>x</p>")
        # Aucune balise réellement ouverte : tout est échappé en texte.
        self.assertNotIn("<script>", html)
        self.assertNotIn("<img src=x", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
        self.assertIn("&lt;img src=x onerror=alert(2)&gt;", html)
        self.assertIn("&lt;b&gt;Titre&lt;/b&gt;", html)
        # Le guillemet est neutralisé : impossible de sortir de l'attribut
        # style= pour injecter un attribut événementiel.
        self.assertNotIn('#fff">', html)
        self.assertIn("&#34;", html)

    # ---------------------------------------------------------- 10. rendu des 4 gabarits
    def test_all_templates_render_in_both_languages(self):
        """Si ça casse : un gabarit lève à l'envoi et la finalisation échoue
        après le téléversement — le pire moment possible pour l'expéditeur."""
        t = self._create(recipient_emails="dest@example.com")
        self._finalize_with_file(t)
        for xid in _TEMPLATE_XIDS:
            tmpl = self.env.ref(xid)
            for lang in ("fr_CA", "en_CA"):
                before = self.Mail.search([])
                try:
                    tmpl.sudo().with_context(lang=lang).send_mail(
                        t.id, force_send=False,
                        email_values={"email_to": "render@i18n-test.example"})
                except Exception as exc:  # noqa: BLE001
                    self.fail("%s ne se rend pas en %s (%s: %s)"
                              % (xid, lang, type(exc).__name__, exc))
                mail = self.Mail.search([]) - before
                self.assertEqual(len(mail), 1, "%s / %s" % (xid, lang))
                body = mail.body_html or ""
                self.assertTrue(body.strip(), "%s / %s : corps vide" % (xid, lang))
                # Le QWeb a bien été exécuté (aucune directive résiduelle).
                for leftover in ("t-out=", "t-if=", "t-foreach=", "t-attf-style="):
                    self.assertNotIn(leftover, body,
                                     "%s / %s : QWeb non rendu (%s)"
                                     % (xid, lang, leftover))
                self.assertIn(t.name, (mail.subject or "") + body,
                              "%s / %s : la référence du transfert manque"
                              % (xid, lang))

    # ------------------------------------------------------------------ 11. _resolve_locale
    def _resolve_with_header(self, header, env=None):
        fake_request = types.SimpleNamespace(
            httprequest=types.SimpleNamespace(
                headers={"Accept-Language": header} if header is not None else {}))
        with patch(MAIN_MOD + ".request", fake_request):
            return st_main._resolve_locale(env=env if env is not None else self.env)

    def test_resolve_locale(self):
        """Si ça casse : la page publique s'affiche en français à un visiteur
        anglophone (ou tente une langue non installée et plante)."""
        self.assertEqual(self._resolve_with_header("en-CA,en;q=0.9"), "en_CA")
        self.assertEqual(self._resolve_with_header("en-US"), "en_CA")
        self.assertEqual(self._resolve_with_header("EN"), "en_CA")
        self.assertEqual(self._resolve_with_header("fr-CA,fr;q=0.9"), "fr_CA")
        self.assertEqual(self._resolve_with_header("fr"), "fr_CA")
        self.assertEqual(self._resolve_with_header(""), "fr_CA")
        self.assertEqual(self._resolve_with_header(None), "fr_CA")
        # En-têtes aberrants
        self.assertEqual(self._resolve_with_header(";;;q=1"), "fr_CA")
        self.assertEqual(self._resolve_with_header("*"), "fr_CA")
        self.assertEqual(self._resolve_with_header("zz-ZZ,klingon"), "fr_CA")
        self.assertEqual(self._resolve_with_header("  ,en"), "fr_CA")

    def test_resolve_locale_falls_back_when_lang_not_installed(self):
        """Si ça casse : on pousse en_CA dans le contexte d'une instance où la
        langue n'est pas activée → rendu incohérent, voire erreur de contexte."""
        class _NoLangEnv:
            def __getitem__(self, name):
                return self

            def sudo(self):
                return self

            def search(self, *args, **kwargs):
                return []

        self.assertEqual(
            self._resolve_with_header("en-CA", env=_NoLangEnv()), "fr_CA")

    def test_resolve_locale_without_request(self):
        """Si ça casse : tout appel hors requête HTTP (cron, test, RPC) lève
        au lieu de retomber sur la langue par défaut."""
        self.assertEqual(st_main._resolve_locale(env=self.env), "fr_CA")

    # ------------------------------------------------------------ 12. parité _UI_STRINGS
    def test_ui_strings_key_parity(self):
        """Si ça casse : une clé absente côté anglais fait retomber le JS sur
        une chaîne française au milieu d'une page anglaise."""
        fr = set(st_main._UI_STRINGS["fr_CA"])
        en = set(st_main._UI_STRINGS["en_CA"])
        self.assertEqual(
            fr, en,
            "manquantes en en_CA : %s ; manquantes en fr_CA : %s"
            % (sorted(fr - en), sorted(en - fr)))
        self.assertEqual(
            set(st_main._UI_STRINGS), {"fr_CA", "en_CA"},
            "les locales servies au JS ne sont plus celles du produit")
        # Les valeurs doivent réellement différer entre les deux langues
        # (sinon l'anglais est du français recopié).
        # « message_label_required » est l'étiquette « Message * » : le mot est
        # le même dans les deux langues, ce n'est pas une traduction manquante.
        tolerated = {"message_label_required"}
        identical = sorted(
            k for k in fr - tolerated
            if st_main._UI_STRINGS["fr_CA"][k] == st_main._UI_STRINGS["en_CA"][k]
        )
        self.assertEqual(
            identical, [],
            "chaînes identiques fr/en (traduction manquante) : %s" % identical)

    # ------------------------------------------------------------ 13. clés lues par le JS
    def test_every_js_string_key_exists(self):
        """Si ça casse : le JS affiche « undefined » à l'utilisateur au moment
        d'une erreur de téléversement."""
        js_path = os.path.join(MODULE_DIR, "static", "src", "js", "st_upload.js")
        self.assertTrue(os.path.exists(js_path), js_path)
        with open(js_path, encoding="utf-8") as fh:
            js = fh.read()
        import re
        used = set(re.findall(r"\bS\.([A-Za-z_][A-Za-z0-9_]*)", js))
        used |= set(re.findall(r"""\bS\[\s*["']([A-Za-z_][A-Za-z0-9_]*)["']\s*\]""", js))
        self.assertTrue(used, "aucune clé S.* trouvée — le regex a dérivé")
        for locale in ("fr_CA", "en_CA"):
            missing = sorted(used - set(st_main._UI_STRINGS[locale]))
            self.assertEqual(
                missing, [],
                "clés lues par st_upload.js absentes de _UI_STRINGS[%s] : %s"
                % (locale, missing))

    def test_ui_strings_have_no_duplicate_literal_keys(self):
        """Si ça casse : deux entrées du dictionnaire partagent une clé, la
        seconde écrase silencieusement la première et un message d'erreur
        utilisateur devient une étiquette de champ (ce fut le cas de
        « message_required », qui affichait « Message * » au lieu de
        « Saisissez un message à envoyer. »)."""
        main_path = os.path.join(MODULE_DIR, "controllers", "main.py")
        with open(main_path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        found = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            if not any(isinstance(tgt, ast.Name) and tgt.id == "_UI_STRINGS"
                       for tgt in node.targets):
                continue
            found = True
            for locale_node, dict_node in zip(node.value.keys, node.value.values):
                keys = [k.value for k in dict_node.keys]
                dupes = sorted({k for k in keys if keys.count(k) > 1})
                self.assertEqual(
                    dupes, [],
                    "clés dupliquées dans _UI_STRINGS[%s] : %s"
                    % (locale_node.value, dupes))
        self.assertTrue(found, "_UI_STRINGS introuvable dans controllers/main.py")

    # ------------------------------------------------------------ 14. hooks._po_template_terms
    def _terms_from_po(self, content):
        tmpdir = tempfile.mkdtemp(prefix="bf_st_po_")
        self.addCleanup(
            lambda: __import__("shutil").rmtree(tmpdir, ignore_errors=True))
        os.makedirs(os.path.join(tmpdir, "i18n"))
        with open(os.path.join(tmpdir, "i18n", "en_CA.po"), "w",
                  encoding="utf-8") as fh:
            fh.write(content)
        fake_os = types.SimpleNamespace(path=types.SimpleNamespace(
            dirname=lambda _p: tmpdir,
            join=os.path.join,
            exists=os.path.exists,
        ))
        with patch.object(hooks, "os", fake_os):
            return hooks._po_template_terms()

    def test_po_template_terms_parsing(self):
        """Si ça casse : les gabarits anglais gardent des phrases françaises —
        un destinataire anglophone reçoit un courriel bilingue incohérent."""
        po = (
            '#. module: bf_securetransfer\n'
            '#: model_terms:mail.template,body_html:'
            'bf_securetransfer.mail_template_transfer_link\n'
            'msgid "Bonjour,"\n'
            'msgstr "Hello,"\n'
            '\n'
            '#. module: bf_securetransfer\n'
            '#: model_terms:mail.template,body_html:'
            'bf_securetransfer.mail_template_transfer_link\n'
            'msgid ""\n'
            '"Ce lien expirera le "\n'
            '"<strong>jour dit</strong>."\n'
            'msgstr ""\n'
            '"This link will expire on "\n'
            '"<strong>that day</strong>."\n'
            '\n'
            '#. module: bf_securetransfer\n'
            '#: model_terms:mail.template,body_html:'
            'bf_securetransfer.mail_template_transfer_receipt\n'
            'msgid "Dites \\"bonjour\\" au dossier C:\\\\\\\\temp"\n'
            'msgstr "Say \\"hello\\" to folder C:\\\\\\\\temp"\n'
            '\n'
            '#. module: bf_securetransfer\n'
            '#: code:addons/bf_securetransfer/models/secure_transfer.py:0\n'
            'msgid "Code invalide."\n'
            'msgstr "Invalid code."\n'
            '\n'
            '#. module: bf_securetransfer\n'
            '#: model_terms:mail.template,body_html:'
            'bf_securetransfer.mail_template_download_notice\n'
            'msgid "Identique"\n'
            'msgstr "Identique"\n'
            '\n'
            '#. module: bf_securetransfer\n'
            '#: model_terms:mail.template,body_html:'
            'bf_securetransfer.mail_template_download_notice\n'
            'msgid "Pas encore traduit"\n'
            'msgstr ""\n'
        )
        terms = self._terms_from_po(po)
        # simple
        self.assertEqual(terms.get("Bonjour,"), "Hello,")
        # msgid/msgstr multi-lignes recollés, balises préservées
        self.assertEqual(
            terms.get("Ce lien expirera le <strong>jour dit</strong>."),
            "This link will expire on <strong>that day</strong>.")
        # échappements \" et \\
        self.assertEqual(
            terms.get('Dites "bonjour" au dossier C:\\\\temp'),
            'Say "hello" to folder C:\\\\temp')
        # un terme hors mail.template n'entre pas dans la carte
        self.assertNotIn("Code invalide.", terms)
        # msgstr identique ou vide → ignoré (sinon remplacement inutile/destructeur)
        self.assertNotIn("Identique", terms)
        self.assertNotIn("Pas encore traduit", terms)
        self.assertEqual(len(terms), 3)

    def test_po_template_terms_missing_file(self):
        """Si ça casse : une installation sans catalogue en_CA plante au
        post_init_hook et le module devient impossible à installer."""
        tmpdir = tempfile.mkdtemp(prefix="bf_st_po_empty_")
        self.addCleanup(
            lambda: __import__("shutil").rmtree(tmpdir, ignore_errors=True))
        fake_os = types.SimpleNamespace(path=types.SimpleNamespace(
            dirname=lambda _p: tmpdir, join=os.path.join, exists=os.path.exists))
        with patch.object(hooks, "os", fake_os):
            self.assertEqual(hooks._po_template_terms(), {})

    def test_po_template_terms_reads_shipped_catalog(self):
        """Si ça casse : le catalogue livré ne produit plus aucun terme et la
        traduction des gabarits devient un no-op silencieux."""
        terms = hooks._po_template_terms()
        self.assertTrue(
            terms, "aucun terme mail.template extrait de i18n/en_CA.po")
        for src, tgt in terms.items():
            self.assertTrue(src and tgt)
            self.assertNotEqual(src, tgt)

    # -------------------------------------------------- 15. apply_email_translations
    def test_apply_email_translations_is_idempotent(self):
        """Si ça casse : chaque mise à niveau du module dégrade un peu plus les
        gabarits anglais (double remplacement, corps tronqué)."""
        hooks.apply_email_translations(self.env)
        self.env.invalidate_all()
        first = {
            xid: self.env.ref(xid).sudo().with_context(lang="en_CA").body_html
            for xid in _TEMPLATE_XIDS
        }
        first_subjects = {
            xid: self.env.ref(xid).sudo().with_context(lang="en_CA").subject
            for xid in _TEMPLATE_XIDS
        }
        hooks.apply_email_translations(self.env)
        self.env.invalidate_all()
        for xid in _TEMPLATE_XIDS:
            tmpl = self.env.ref(xid).sudo().with_context(lang="en_CA")
            self.assertEqual(
                str(tmpl.body_html), str(first[xid]),
                "%s : le corps en_CA change au second passage" % xid)
            self.assertEqual(tmpl.subject, first_subjects[xid],
                             "%s : l'objet en_CA change au second passage" % xid)
            self.assertTrue(str(tmpl.body_html).strip(),
                            "%s : corps en_CA vide" % xid)
            self.assertIn("{{ object.name }}", tmpl.subject or "",
                          "%s : l'objet anglais perd la référence" % xid)
            # La langue forcée est bien libérée : c'est elle qui empêchait la
            # langue du contact de gagner à l'envoi.
            self.assertFalse(self.env.ref(xid).sudo().lang)
