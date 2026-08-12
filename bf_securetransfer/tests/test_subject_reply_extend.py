"""18.0.1.17.0 — l'objet, le Reply-To, le renvoi de lien et la prolongation.

Trois choses que le socle ne savait pas faire, et qui se cassent en silence si
personne ne les surveille :

* **l'objet** part dans un en-tête de courriel — donc il doit être nettoyé
  (CR/LF) et il doit rester HORS des portes (mot de passe, code destinataire),
  puisqu'il est justement la ligne qui dit au destinataire de quoi il s'agit ;
* le **Reply-To** doit désigner l'expéditeur humain, sinon une réponse atterrit
  dans la boîte de marque et personne ne la voit ;
* la **prolongation** doit rester sur la grille de durées offerte par la
  marque : c'est cette grille qui fixe le filet de cycle de vie du seau, et une
  date hors grille promet un jour que le fournisseur de stockage ne tiendra pas.

S3 est entièrement bouchonné, comme partout ailleurs dans la suite.
"""
from datetime import timedelta

from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged

S3_MOD = "odoo.addons.bf_securetransfer.models.s3"


@tagged("post_install", "-at_install")
class TestSubjectReplyExtend(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.brand = cls.env.ref("bf_securetransfer.brand_default")
        icp = cls.env["ir.config_parameter"].sudo()
        icp.set_param("bf_securetransfer.quota_daily_transfers_per_ip", "500")
        icp.set_param("bf_securetransfer.quota_daily_transfers_per_sender", "500")
        icp.set_param("bf_securetransfer.quota_daily_bytes_per_ip_mb", "1000000")
        # Réglages posés EXPLICITEMENT plutôt que supposés : sur une copie de
        # locataire, la marque est « payante » et le code destinataire est
        # actif, ce qui faisait échouer des tests sans qu'aucun code soit en
        # cause (le piège documenté dans le README).
        icp.set_param("bf_securetransfer.require_recipient_otp", "0")
        icp.set_param("bf_securetransfer.require_sender_otp", "0")
        icp.set_param("bf_securetransfer.default_sender_allowlist", "")
        icp.set_param("bf_securetransfer.default_recipient_allowlist", "")
        # 30 jours offerts : sans ça, la grille tombe à [1, 7] et il ne reste
        # aucun palier au-dessus de 7 pour éprouver la prolongation.
        cls.brand.sudo().write({"max_retention_days": 30, "allow_burn": True})
        cls.env["res.lang"].sudo()._activate_lang("en_CA")

        internal = cls.env.ref("base.group_user")
        cls.g_user = cls.env.ref("bf_securetransfer.group_securetransfer_user")
        cls.g_manager = cls.env.ref(
            "bf_securetransfer.group_securetransfer_manager")
        cls.reader = cls.env["res.users"].create({
            "name": "st-sre-reader", "login": "st-sre-reader",
            "groups_id": [(6, 0, [internal.id, cls.g_user.id])],
        })
        cls.manager = cls.env["res.users"].create({
            "name": "st-sre-manager", "login": "st-sre-manager",
            "groups_id": [(6, 0, [internal.id, cls.g_manager.id])],
        })

    def setUp(self):
        super().setUp()
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
        vals.update(overrides)
        return self.env["secure.transfer"].api_create(
            self.brand, vals, "203.0.113.10", "test-suite/1.0", "fr_CA",
        )

    def _head_for(self, transfer):
        sizes = {f.s3_key: int(f.size) for f in transfer.file_ids}

        def _head(env, key):
            if key in sizes:
                return {"size": sizes[key], "etag": "etag-" + key[-8:]}
            return None
        return _head

    def _finalize_with_file(self, transfer, filename="rapport.pdf", size=4096):
        transfer._register_file(filename, size)
        with patch(S3_MOD + ".head_object", side_effect=self._head_for(transfer)):
            return transfer.action_finalize()

    def _new_mails(self):
        return self.Mail.search([]) - self._mails_before

    def _to(self, address):
        return self._new_mails().filtered(
            lambda m: (m.email_to or "").strip() == address)

    # ================================================================== objet
    def test_subject_is_stored_cleaned(self):
        """Si ça casse : un objet à rallonge ou porteur d'un saut de ligne part
        dans un en-tête de courriel — troncature chez le destinataire au mieux,
        injection d'en-tête au pire."""
        t = self._create(subject="  Baux 2026\r\nBcc: pirate@example.com  ")
        self.assertEqual(t.subject, "Baux 2026Bcc: pirate@example.com")
        self.assertNotIn("\n", t.subject)
        self.assertNotIn("\r", t.subject)

        long_subject = "x" * 400
        t2 = self._create(subject=long_subject)
        self.assertEqual(len(t2.subject), 120)

    def test_subject_optional_nothing_changes(self):
        """Si ça casse : les transferts sans objet — c'est-à-dire tous ceux
        d'avant 1.17.0 — verraient leur objet de courriel changer de forme."""
        t = self._create(subject="")
        self._finalize_with_file(t)
        mail = self._to("dest@example.com")
        self.assertEqual(len(mail), 1)
        self.assertIn("vous a transmis des fichiers", mail.subject or "")
        self.assertIn(t.name, mail.subject or "")

    def test_subject_leads_the_link_email(self):
        """Si ça casse : le destinataire lit « TRF-2026-00123 » dans sa liste de
        courriels et n'a aucune idée de ce qu'on lui envoie."""
        t = self._create(subject="Baux 2026")
        self._finalize_with_file(t)
        mail = self._to("dest@example.com")
        self.assertEqual(len(mail), 1)
        self.assertTrue((mail.subject or "").startswith("Baux 2026 — "),
                        "l'objet doit MENER la ligne : %r" % mail.subject)
        self.assertIn("vous a transmis des fichiers", mail.subject or "")
        self.assertIn(t.name, mail.subject or "")
        # …et il est repris en tête du corps.
        self.assertIn("Baux 2026", mail.body_html or "")

    def test_subject_on_the_receipt(self):
        """Si ça casse : l'expéditeur ne retrouve plus son propre envoi dans sa
        boîte, alors que c'est lui qui a écrit l'objet."""
        t = self._create(subject="Baux 2026")
        self._finalize_with_file(t)
        receipt = self._to("sender@example.com")
        self.assertEqual(len(receipt), 1)
        self.assertIn("Baux 2026", receipt.subject or "")
        self.assertIn(t.name, receipt.subject or "")

    def test_subject_travels_but_the_gated_body_still_does_not(self):
        """Si ça casse : soit l'objet disparaît quand une porte est armée (le
        destinataire n'a plus AUCUN indice), soit — bien pire — le corps retenu
        repart avec lui dans la boîte."""
        t = self._create(subject="Dossier Tremblay", message="Mot de passe : hunter2")
        t.force_recipient_otp = True
        self._finalize_with_file(t)
        mail = self._to("dest@example.com")
        self.assertEqual(len(mail), 1)
        self.assertIn("Dossier Tremblay", mail.subject or "")
        self.assertIn("message sécurisé", mail.subject or "")
        self.assertNotIn("hunter2", mail.body_html or "",
                         "le corps retenu a fui dans le courriel")

    def test_subject_on_the_download_notice(self):
        """Si ça casse : l'avis « vos fichiers ont été téléchargés » ne dit pas
        LESQUELS quand on en a plusieurs en vol."""
        t = self._create(subject="Baux 2026", notify_on_download=True)
        self._finalize_with_file(t)
        before = self._new_mails()
        t._notify_download()
        notice = (self._new_mails() - before).filtered(
            lambda m: "téléchargé" in (m.subject or ""))
        self.assertTrue(notice)
        self.assertIn("Baux 2026", notice[0].subject or "")

    def test_finalize_revalidates_the_subject(self):
        """Si ça casse : le nettoyage n'existe qu'au create, et un client qui
        pose son objet au finalize passe à côté (deux points d'entrée, une
        seule garde — le bogue classique de ce module)."""
        t = self._create(subject="")
        t._register_file("doc.pdf", 4096)
        from odoo.addons.bf_securetransfer.controllers import upload_api
        cleaned = self.env["secure.transfer"]._clean_line(
            "Reçu\r\nX-Injecte: 1", upload_api.MAX_SUBJECT_CHARS)
        self.assertNotIn("\n", cleaned)
        self.assertNotIn("\r", cleaned)

    # ============================================================== reply-to
    def test_reply_to_stays_off_on_an_open_brand(self):
        """Si ça casse : sur une marque ouverte à tout expéditeur — l'état réel
        d'un palier gratuit public — n'importe qui fait partir un courriel
        brandé, signé DKIM, vers dix adresses de son choix. Lui donner le
        Reply-To finirait le travail : la réponse irait à lui plutôt que de
        revenir à la boîte de marque, seul endroit où cet abus se voit."""
        self.brand.sudo().write({"sender_allowlist": ""})
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_securetransfer.default_sender_allowlist", "")
        t = self._create(subject="Baux 2026")
        self._finalize_with_file(t)
        mail = self._to("dest@example.com")
        self.assertEqual(len(mail), 1)
        self.assertFalse(mail.reply_to,
                         "Reply-To posé sur une marque qui accepte n'importe "
                         "quel expéditeur : %r" % mail.reply_to)

    def test_reply_to_is_set_when_the_brand_vouches_for_the_sender(self):
        """Une liste d'expéditeurs, c'est l'opérateur qui dit qu'il répond de
        qui la franchit."""
        self.brand.sudo().write({"sender_allowlist": "@example.com"})
        t = self._create(subject="Baux 2026")
        self._finalize_with_file(t)
        mail = self._to("dest@example.com")
        self.assertIn("sender@example.com", mail.reply_to or "")
        self.assertIn("Test Sender", mail.reply_to or "")
        # Le From reste la marque : seul l'en-tête de courtoisie bouge, donc
        # SPF/DKIM/DMARC ne sont pas touchés.
        self.assertNotIn("sender@example.com", mail.email_from or "")

    def test_reply_to_is_set_on_a_drop_page(self):
        """Le cas pour lequel l'en-tête existe : un dépôt ne peut atteindre que
        le propriétaire de la page, qui l'a demandé — répondre au déposant n'a
        aucune valeur d'hameçonnage."""
        drop = self.env["secure.transfer.brand"].sudo().create({
            "name": "Dépôt test sécurité",
            "slug": "depot-test-securite-reply",
            "fixed_recipient": "proprio@example.com",
        })
        t = self.env["secure.transfer"].api_create(
            drop, {"sender_name": "Déposant", "sender_email": "depot@ailleurs.com",
                   "message": "voici", "retention_days": 7},
            "203.0.113.11", "test-suite/1.0", "fr_CA")
        self._finalize_with_file(t)
        mail = self._to("proprio@example.com")
        self.assertEqual(len(mail), 1)
        self.assertIn("depot@ailleurs.com", mail.reply_to or "")

    def test_reply_to_is_set_for_a_backend_send(self):
        """Un envoi parti du backend vient d'un utilisateur interne."""
        t = self._create(subject="")
        t.sudo().ip_created = "backend"
        self._finalize_with_file(t)
        mail = self._to("dest@example.com")
        self.assertIn("sender@example.com", mail.reply_to or "")

    # ================================================== bureau d'abus (1.17.2)
    def test_abuse_desk_uses_the_tenant_setting_first(self):
        icp = self.env["ir.config_parameter"].sudo()
        icp.set_param("bf_securetransfer.abuse_email", "abus@exemple.invalid")
        self.addCleanup(icp.set_param, "bf_securetransfer.abuse_email", "")
        t = self._create()
        self._finalize_with_file(t)
        self.assertEqual(t._abuse_desk_email(), "abus@exemple.invalid")

    def test_abuse_desk_falls_back_to_the_tenant_company(self):
        """Si ça casse : l'avis d'abus — qui porte l'expéditeur, la LISTE
        COMPLÈTE des destinataires, le motif et l'IP du signalant — repart vers
        une adresse codée en dur, donc vers une AUTRE organisation que celle
        qui héberge le transfert."""
        icp = self.env["ir.config_parameter"].sudo()
        icp.set_param("bf_securetransfer.abuse_email", "")
        t = self._create()
        self._finalize_with_file(t)
        societe = t.company_id or self.env.company
        societe.sudo().email = "info@locataire.invalid"
        self.assertEqual(t._abuse_desk_email(), "info@locataire.invalid")

    def test_abuse_desk_never_names_an_operator(self):
        """La garde de fond : aucune adresse de l'éditeur ne doit rester dans
        le code, ni dans le réglage par défaut."""
        icp = self.env["ir.config_parameter"].sudo()
        icp.set_param("bf_securetransfer.abuse_email", "")
        t = self._create()
        self._finalize_with_file(t)
        societe = t.company_id or self.env.company
        societe.sudo().email = "info@locataire.invalid"
        self.assertEqual(t._abuse_desk_email(), "info@locataire.invalid")
        champ = self.env["res.config.settings"]._fields["st_abuse_email"]
        self.assertFalse(
            getattr(champ, "default", None),
            "le réglage porte encore un défaut codé en dur")

    def test_abuse_desk_last_resort_is_never_empty(self):
        """Un email_to vide échoue en silence dans la file : le dernier repli
        doit toujours rendre quelque chose."""
        icp = self.env["ir.config_parameter"].sudo()
        icp.set_param("bf_securetransfer.abuse_email", "")
        t = self._create()
        self._finalize_with_file(t)
        societe = t.company_id or self.env.company
        societe.sudo().email = False
        self.assertTrue((t._abuse_desk_email() or "").strip())

    def test_abuse_notice_goes_to_the_resolved_desk(self):
        icp = self.env["ir.config_parameter"].sudo()
        icp.set_param("bf_securetransfer.abuse_email", "abus@exemple.invalid")
        self.addCleanup(icp.set_param, "bf_securetransfer.abuse_email", "")
        t = self._create()
        self._finalize_with_file(t)
        avant = self._new_mails()
        t._suspend_for_abuse(ip="203.0.113.9")
        t._send_abuse_notice(reason="essai", ip="203.0.113.9")
        frais = self._new_mails() - avant
        desk = frais.filtered(lambda m: (m.email_to or "") == "abus@exemple.invalid")
        self.assertEqual(len(desk), 1)
        self.assertIn("203.0.113.9", desk.body_html or "")
        # …et aucun courriel ne part ailleurs que vers le bureau d'abus
        # résolu ou les destinataires du transfert : plus aucune adresse
        # codée en dur ne peut s'inviter dans la liste.
        attendues = {"abus@exemple.invalid"} | set(t._recipient_list())
        self.assertFalse(
            frais.filtered(lambda m: (m.email_to or "").strip() not in attendues),
            frais.mapped("email_to"))

    # =================================================== en-têtes de courriel
    def test_header_fields_are_line_cleaned_by_the_orm(self):
        """Si ça casse : un CR/LF dans un champ qui part en en-tête fait lever
        Python (« Header values may not contain linefeed ») — donc le courriel
        n'est PAS injecté, il n'est **jamais livré** : le mail.mail tombe en
        `exception` alors que l'expéditeur a déjà lu « transfert prêt ».
        Le nettoyage doit vivre dans create/write, pas dans un contrôleur : la
        route de finalisation écrivait le nom d'expéditeur en direct."""
        t = self._create()
        t.sudo().write({
            "sender_name": "Legit\r\nBcc: pirate@evil.invalid",
            "subject": "Objet\r\nX-Injecte: 1",
            "recipient_emails": "dest@example.com\r\nBcc: pirate@evil.invalid",
        })
        for field in ("sender_name", "subject", "recipient_emails"):
            value = t[field] or ""
            self.assertNotIn("\r", value, "%s garde un CR" % field)
            self.assertNotIn("\n", value, "%s garde un LF" % field)

    def test_header_fields_are_capped_by_the_orm(self):
        """Si ça casse : un objet de 5 000 caractères passe par un write ORM,
        et le plafond du formulaire public ne borne plus rien."""
        t = self._create()
        t.sudo().write({"subject": "x" * 5000, "sender_name": "y" * 5000})
        self.assertLessEqual(len(t.subject or ""), 120)
        self.assertLessEqual(len(t.sender_name or ""), 128)

    def test_the_finalize_route_can_no_longer_store_a_dirty_name(self):
        """Le trou exact : `_clean_sender_name` du contrôleur ne filtre que les
        espaces. La garde du modèle doit rattraper."""
        from odoo.addons.bf_securetransfer.controllers import upload_api
        t = self._create()
        t.sudo().write({
            "sender_name": upload_api._clean_sender_name(
                "Legit\r\nBcc: pirate@evil.invalid")})
        self.assertNotIn("\n", t.sender_name or "")

    def test_a_dirty_transfer_still_produces_a_sendable_message(self):
        """La preuve de bout en bout : le message MIME se construit, donc le
        courriel part. Avant la garde, `build_email` levait une ValueError et
        le transfert restait muet."""
        self.brand.sudo().write({"sender_allowlist": "@example.com"})
        t = self._create(subject="Objet\r\nX-Injecte: 1")
        t.sudo().write({"sender_name": "Legit\r\nBcc: pirate@evil.invalid"})
        self._finalize_with_file(t)
        mail = self._to("dest@example.com")
        self.assertEqual(len(mail), 1)
        msg = self.env["ir.mail_server"].sudo().build_email(
            email_from="bonjour@exemple.test", email_to=[mail.email_to],
            subject=mail.subject or "", body="<p>corps</p>", subtype="html",
            reply_to=mail.reply_to or None)
        # Le test porte sur les en-têtes RÉELLEMENT créés, pas sur la présence
        # du texte : « Bcc: » survit comme SUITE de caractères dans la valeur
        # d'un en-tête légitime, et c'est inoffensif. Ce qui compte est qu'il ne
        # commence aucune ligne d'en-tête à lui seul.
        self.assertEqual(set(msg.keys()) & {"Bcc", "X-Injecte"}, set(),
                         "un en-tête a été créé depuis une valeur de champ")
        for value in msg.values():
            self.assertNotIn("\n", str(value))
            self.assertNotIn("\r", str(value))

    # ============================================================ renvoi lien
    def test_resend_is_manager_only(self):
        """Si ça casse : un compte en LECTURE SEULE peut faire poster le lien
        de partage par le serveur, via XML-RPC — la méthode est une surface
        RPC, le `groups=` du bouton ne garde que l'interface."""
        t = self._create()
        self._finalize_with_file(t)
        try:
            t.with_user(self.reader).action_resend_emails()
        except (UserError, AccessError):
            pass
        else:
            self.fail("un lecteur ne doit pas pouvoir renvoyer le lien")

    def test_resend_recipients_only(self):
        """Si ça casse : l'accusé repart aussi vers l'expéditeur — du bruit, et
        le lien de partage reposté une seconde fois dans une boîte qui l'a
        déjà."""
        t = self._create(subject="Baux 2026")
        self._finalize_with_file(t)
        before = self._new_mails()
        t.with_user(self.manager).action_resend_emails()
        fresh = self._new_mails() - before
        self.assertEqual(len(fresh), 1)
        self.assertEqual((fresh.email_to or "").strip(), "dest@example.com")
        self.assertIn("Baux 2026", fresh.subject or "")
        # …et le renvoi est journalisé, avec l'opérateur nommé.
        last = t.access_log_ids.sorted("id")[-1]
        self.assertEqual(last.action, "emailed")
        self.assertEqual(last.actor, self.manager.login)
        self.assertIn("Renvoi manuel", last.note or "")

    def test_resend_refuses_link_only_mode(self):
        """Si ça casse : le bouton répond « fait » alors qu'il n'a écrit à
        personne (aucun destinataire enregistré)."""
        t = self._create(recipient_emails="")
        self._finalize_with_file(t)
        with self.assertRaises(UserError):
            t.with_user(self.manager).action_resend_emails()

    def test_resend_refuses_when_not_active(self):
        t = self._create()
        self._finalize_with_file(t)
        t.action_expire_now()
        with self.assertRaises(UserError):
            t.with_user(self.manager).action_resend_emails()

    # ============================================================ prolongation
    def test_extension_choices_only_go_up(self):
        """Si ça casse : « prolonger » proposerait de RACCOURCIR la durée, ce
        qui couperait l'accès plus tôt que ce qui a été promis."""
        t = self._create(retention_days=7)
        self._finalize_with_file(t)
        self.assertEqual(t._extension_choices(), [30])

    def test_extend_moves_the_deadline_from_the_send_date(self):
        """Si ça casse : la nouvelle échéance se compte à partir d'aujourd'hui,
        donc la rétention affichée ne veut plus rien dire — et peut sortir du
        filet de cycle de vie posé sur le seau."""
        t = self._create(retention_days=7)
        self._finalize_with_file(t)
        t.with_user(self.manager).extend_expiry(30)
        self.assertEqual(t.retention_days, 30)
        expected = t.finalized_at + timedelta(days=30)
        self.assertEqual(t.expiry_date, expected)
        last = t.access_log_ids.sorted("id")[-1]
        self.assertEqual(last.action, "extended")
        self.assertEqual(last.actor, self.manager.login)

    def test_extend_reopens_an_expired_transfer(self):
        """Si ça casse : le seul recours quand un client rate la fenêtre est de
        tout refaire téléverser — alors que les objets sont encore là."""
        t = self._create(retention_days=7)
        self._finalize_with_file(t)
        t.sudo().write({
            "state": "expired",
            "finalized_at": fields.Datetime.now() - timedelta(days=10),
            "expiry_date": fields.Datetime.now() - timedelta(days=3),
        })
        t.with_user(self.manager).extend_expiry(30)
        self.assertEqual(t.state, "active")
        self.assertGreater(t.expiry_date, fields.Datetime.now())
        self.assertIn("rouvert", t.access_log_ids.sorted("id")[-1].note or "")

    def test_extend_refuses_a_duration_off_the_brand_grid(self):
        """Si ça casse : on promet une date que le cycle de vie du seau ne
        tiendra pas — les fichiers disparaissent avant l'échéance annoncée."""
        t = self._create(retention_days=7)
        self._finalize_with_file(t)
        with self.assertRaises(UserError):
            t.with_user(self.manager).extend_expiry(45)
        with self.assertRaises(UserError):
            t.with_user(self.manager).extend_expiry(1)

    def test_extend_refuses_when_still_short_of_today(self):
        """Si ça casse : on « prolonge » un transfert qui reste échu, et le
        gestionnaire croit l'avoir rouvert."""
        t = self._create(retention_days=7)
        self._finalize_with_file(t)
        t.sudo().write({
            "state": "expired",
            "finalized_at": fields.Datetime.now() - timedelta(days=120),
        })
        with self.assertRaises(UserError):
            t.with_user(self.manager).extend_expiry(30)

    def test_extend_refuses_a_consumed_burn(self):
        """Si ça casse : un envoi « détruire après lecture » redevient lisible —
        l'inverse exact de ce que l'expéditeur a demandé."""
        t = self._create(retention_days=7)
        # api_create ne recopie QUE les clés qu'il connaît : le burn se pose
        # au finalize (formulaire public) ou ici, jamais via ses vals.
        t.sudo().burn_after_download = True
        self._finalize_with_file(t)
        t.sudo().write({"state": "expired"})
        with self.assertRaises(UserError):
            t.with_user(self.manager).extend_expiry(30)

    def test_extend_refuses_a_spent_download_budget(self):
        """Si ça casse : la date bouge, le lien reste fermé, et rien ne dit
        pourquoi."""
        t = self._create(retention_days=7, max_downloads=2)
        self._finalize_with_file(t)
        t.sudo().write({"state": "expired", "download_count": 2})
        with self.assertRaises(UserError):
            t.with_user(self.manager).extend_expiry(30)

    def test_extend_refuses_after_purge(self):
        t = self._create(retention_days=7)
        self._finalize_with_file(t)
        t.sudo().write({
            "state": "expired", "purged_at": fields.Datetime.now(),
        })
        with self.assertRaises(UserError):
            t.with_user(self.manager).extend_expiry(30)

    def test_extend_is_manager_only(self):
        """Même raison que le renvoi : méthode publique = surface RPC."""
        t = self._create(retention_days=7)
        self._finalize_with_file(t)
        for call in (lambda: t.with_user(self.reader).extend_expiry(30),
                     lambda: t.with_user(self.reader).action_extend_expiry()):
            try:
                call()
            except (UserError, AccessError):
                continue
            self.fail("un lecteur ne doit pas pouvoir prolonger")

    def test_wizard_refuses_when_already_at_the_longest_tier(self):
        """Si ça casse : l'assistant s'ouvre sur une liste vide et le
        gestionnaire ne comprend pas pourquoi rien ne se passe."""
        t = self._create(retention_days=30)
        self._finalize_with_file(t)
        Wizard = self.env["secure.transfer.extend.wizard"].with_user(self.manager)
        with self.assertRaises(UserError):
            Wizard.with_context(default_transfer_id=t.id).create({})

    def test_wizard_applies_the_chosen_tier(self):
        t = self._create(retention_days=7)
        self._finalize_with_file(t)
        Wizard = self.env["secure.transfer.extend.wizard"].with_user(self.manager)
        wiz = Wizard.with_context(default_transfer_id=t.id).create(
            {"transfer_id": t.id, "days": "30"})
        self.assertEqual(wiz.new_expiry, t.finalized_at + timedelta(days=30))
        wiz.action_apply()
        self.assertEqual(t.retention_days, 30)
