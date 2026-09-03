"""Audience ouverte : le lien qui ne nomme personne.

Ce mode retourne la question du code à usage unique. En destinataires nommés,
le code PROUVE qu'on est l'une des adresses connues ; ici, il EST le contrôle
d'accès — le visiteur se déclare, et seule la confirmation l'admet.

Ce que cette suite tient, dans l'ordre de ce qui coûterait le plus cher :

* **le lien ne devient jamais public** — le code est forcé, quoi que disent le
  drapeau par transfert et le réglage d'instance ;
* **le lien ne devient jamais un relais** — plafond de visiteurs distincts,
  plafond de codes par identité, temporisation, liste blanche ; tous en base,
  parce qu'un compteur en mémoire vaut le nombre de workers ;
* **une confirmation prouve quelque chose** — le code part sur le canal
  déclaré et sur aucun autre. Un repli du SMS vers le courriel donnerait
  l'accès à qui a tapé l'adresse d'un tiers ;
* **le mode nommé ne bouge pas** — il porte tout le trafic existant.

S3 et VoIP.ms sont bouchonnés : la suite doit tourner sans réseau.
"""
import io
from unittest.mock import MagicMock, patch

from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged

from .common import BaseNeuve

S3_MOD = "odoo.addons.bf_securetransfer.models.s3"
SMS_MOD = "odoo.addons.bf_securetransfer.models.sms"
TRANSFER = "odoo.addons.bf_securetransfer.models.secure_transfer.SecureTransfer"


def _minimal_pdf():
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    doc = canvas.Canvas(buf)
    doc.drawString(72, 720, "QA")
    doc.showPage()
    doc.save()
    return buf.getvalue()


@tagged("post_install", "-at_install")
class TestOpenAudience(BaseNeuve, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.brand = cls.env.ref("bf_securetransfer.brand_default")
        cls.brand.write({
            "allow_open_audience": True,
            "allow_audience_sms": True,
            "audience_max_default": 50,
            "audience_domains": False,
        })
        icp = cls.env["ir.config_parameter"].sudo()
        icp.set_param("bf_securetransfer.quota_daily_transfers_per_ip", "500")
        icp.set_param("bf_securetransfer.quota_daily_transfers_per_sender", "500")
        icp.set_param("bf_securetransfer.quota_daily_bytes_per_ip_mb", "1000000")
        icp.set_param("bf_securetransfer.require_recipient_otp", "0")

    # ------------------------------------------------------------------ helpers
    def _transfer(self, **overrides):
        vals = {
            "sender_name": "Test Sender",
            "sender_email": "sender@example.com",
            "recipient_emails": "",
            "message": "Bonjour",
            "retention_days": 7,
        }
        vals.update(overrides)
        rec = self.env["secure.transfer"].api_create(
            self.brand, vals, "203.0.113.10", "test-suite/1.0", "fr_CA",
        )
        return rec

    def _open_transfer(self, **overrides):
        audience = {
            "audience_mode": "open",
            "audience_max": 0,
            "audience_allow_sms": True,
        }
        audience.update(overrides)
        t = self._transfer()
        t.write(audience)
        return t

    def _head_for(self, transfer):
        sizes = {f.s3_key: int(f.size) for f in transfer.file_ids}

        def _head(env, key):
            if key in sizes:
                return {"size": sizes[key], "etag": "etag-" + key[-8:]}
            return None
        return _head

    def _active_open(self, filename="contrat.pdf", size=4096, **overrides):
        t = self._open_transfer(**overrides)
        t._register_file(filename, size)
        with patch(S3_MOD + ".head_object", side_effect=self._head_for(t)):
            t.action_finalize()
        return t

    # ------------------------------------------------------------------ garde-fous du mode
    def test_open_mode_requires_the_brand_to_offer_it(self):
        """La marque est le seul endroit où l'opérateur a consenti au mode.
        Sans elle, un appel ORM ou XML-RPC ouvrirait un lien tout seul."""
        self.brand.allow_open_audience = False
        t = self._transfer()
        with self.assertRaises(ValidationError):
            t.audience_mode = "open"

    def test_open_mode_forces_the_recipient_code(self):
        """Un lien qui ne nomme personne et ne demande pas de code est un lien
        public. Le prédicat doit le forcer, drapeau et réglage éteints."""
        t = self._open_transfer()
        self.assertFalse(t.force_recipient_otp)
        self.assertFalse(t._needs_recipient_otp_param())
        self.assertTrue(t._recipient_otp_required())
        self.assertEqual(t.recipient_otp_status, "audience")

    def test_declared_mode_still_refuses_an_unknown_address(self):
        """Régression : tout le trafic existant passe par là."""
        t = self._transfer(recipient_emails="connu@example.com")
        self.assertTrue(t._is_recipient_email("connu@example.com"))
        self.assertFalse(t._is_recipient_email("inconnu@example.com"))

    def test_open_mode_accepts_an_undeclared_address(self):
        """C'est la fonctionnalité elle-même."""
        t = self._open_transfer()
        self.assertTrue(t._is_recipient_email("inconnu@example.com"))

    # ------------------------------------------------------------------ liste blanche
    def test_allowlist_refuses_a_foreign_domain(self):
        t = self._open_transfer(audience_domains="@client.com")
        ok, reason = t._audience_admissible("email", "jean@client.com")
        self.assertTrue(ok)
        ko, reason = t._audience_admissible("email", "jean@ailleurs.com")
        self.assertFalse(ko)
        self.assertEqual(reason, "domain")

    def test_transfer_allowlist_wins_over_the_brand(self):
        self.brand.audience_domains = "@marque.com"
        t = self._open_transfer(audience_domains="@transfert.com")
        self.assertTrue(t._audience_admissible("email", "a@transfert.com")[0])
        self.assertFalse(t._audience_admissible("email", "a@marque.com")[0])

    def test_brand_allowlist_falls_back_to_the_recipient_allowlist(self):
        """Une instance qui restreint déjà ses destinataires ne s'ouvre pas par
        le seul choix du mode : ce serait un contournement de l'anti-rebond."""
        self.brand.audience_domains = False
        self.brand.recipient_allowlist = "@client.com"
        t = self._open_transfer()
        self.assertTrue(t._audience_admissible("email", "a@client.com")[0])
        self.assertFalse(t._audience_admissible("email", "a@ailleurs.com")[0])
        self.brand.recipient_allowlist = False

    def test_a_domain_allowlist_refuses_mobile_identities(self):
        """Une liste de domaines ne sait rien dire d'un numéro. La laisser
        passer sans contrôle serait une porte ouverte au milieu d'une règle
        que l'opérateur croit appliquée.

        Le canal est bouchonné ACTIF : sans cela le refus viendrait du canal
        (« sms non câblé ») et le test passerait sans rien prouver de la règle
        qu'il prétend tenir."""
        t = self._open_transfer(audience_domains="@client.com")
        with patch(SMS_MOD + ".configured", return_value=True):
            ok, reason = t._audience_admissible("sms", "5145551234")
        self.assertFalse(ok)
        self.assertEqual(reason, "domain")

    # ------------------------------------------------------------------ plafonds
    def test_visitor_cap_refuses_a_new_identity_but_not_an_existing_one(self):
        """Le 3ᵉ visiteur ne doit pas fermer la porte aux deux premiers."""
        t = self._open_transfer(audience_max=2)
        a = t._audience_join("email", "un@example.com", ip="203.0.113.1")
        b = t._audience_join("email", "deux@example.com", ip="203.0.113.1")
        self.assertTrue(a and b)
        refuse, reason = t._audience_admissible("email", "trois@example.com")
        self.assertFalse(refuse)
        self.assertEqual(reason, "full")
        # Les inscrits repassent
        self.assertTrue(t._audience_admissible("email", "un@example.com")[0])

    def test_pending_visitors_count_against_the_cap(self):
        """Sinon le plafond ne limiterait que les confirmations, et l'envoi de
        codes — le coût et le risque réels — resterait sans borne."""
        t = self._open_transfer(audience_max=1)
        t._audience_join("email", "jamais-confirme@example.com")
        self.assertEqual(t.audience_ids.state, "pending")
        self.assertEqual(t.audience_count, 0)
        self.assertFalse(t._audience_admissible("email", "autre@example.com")[0])

    def test_join_is_idempotent(self):
        """Deux demandes de code pour la même adresse = une ligne, un budget."""
        t = self._open_transfer()
        first = t._audience_join("email", "Jean@Example.com")
        again = t._audience_join("email", "jean@example.com")
        self.assertEqual(first, again)
        self.assertEqual(len(t.audience_ids), 1)

    def test_otp_cap_per_identity(self):
        """Le plafond par identité est ce qui empêche d'utiliser le lien comme
        relais de courriel vers un tiers."""
        t = self._open_transfer()
        member = t._audience_join("email", "cible@example.com")
        member.sudo().otp_send_count = member.MAX_OTP_PER_IDENTITY
        allowed, reason = member._may_receive_otp()
        self.assertFalse(allowed)
        self.assertEqual(reason, "otp_cap")

    def test_otp_cooldown_blocks_an_immediate_resend(self):
        t = self._open_transfer()
        member = t._audience_join("email", "cible@example.com")
        with patch.object(type(t), "_otp_email", return_value=None):
            otp_hash, _exp, _m = t._send_audience_otp("email", "cible@example.com")
        self.assertTrue(otp_hash)
        allowed, reason = member._may_receive_otp()
        self.assertFalse(allowed)
        self.assertEqual(reason, "cooldown")

    def test_sms_cap_is_lower_than_the_email_cap(self):
        """Un SMS coûte réellement de l'argent."""
        Audience = self.env["secure.transfer.audience"]
        self.assertLess(Audience.MAX_SMS_PER_IDENTITY,
                        Audience.MAX_OTP_PER_IDENTITY)

    # ------------------------------------------------------------------ canal SMS
    def test_mobile_identity_refused_when_the_brand_does_not_offer_it(self):
        self.brand.allow_audience_sms = False
        t = self._open_transfer()
        with patch(SMS_MOD + ".configured", return_value=True):
            ok, reason = t._audience_admissible("sms", "5145551234")
        self.assertFalse(ok)
        self.assertEqual(reason, "channel")
        self.brand.allow_audience_sms = True

    def test_mobile_identity_refused_when_the_channel_is_not_wired(self):
        """Offrir un canal qui n'enverra rien laisserait le visiteur devant un
        formulaire qui ne répond jamais."""
        t = self._open_transfer()
        with patch(SMS_MOD + ".configured", return_value=False):
            ok, reason = t._audience_admissible("sms", "5145551234")
        self.assertFalse(ok)
        self.assertEqual(reason, "channel")

    def test_mobile_identity_is_delivered_by_sms(self):
        t = self._open_transfer()
        with patch(SMS_MOD + ".configured", return_value=True), \
                patch(SMS_MOD + ".send", return_value=True) as sent, \
                patch.object(type(t), "_otp_email") as mailed:
            otp_hash, _exp, member = t._send_audience_otp("sms", "514 555-1234")
        self.assertTrue(otp_hash)
        self.assertTrue(sent.called, "le code doit partir par SMS")
        self.assertFalse(mailed.called, "aucun courriel ne doit partir")
        self.assertEqual(member.identity_kind, "sms")
        self.assertEqual(member.phone, "5145551234")
        self.assertEqual(member.sms_send_count, 1)

    def test_a_failed_sms_never_falls_back_to_email(self):
        """⚠ Le point le plus important de la suite. En audience ouverte,
        l'identité EST le numéro : livrer ailleurs donnerait l'accès à
        quelqu'un d'autre. Le mode nommé, lui, a le droit de replier — la
        personne y est connue d'avance."""
        t = self._open_transfer()
        with patch(SMS_MOD + ".configured", return_value=True), \
                patch(SMS_MOD + ".send", return_value=False), \
                patch.object(type(t), "_otp_email") as mailed:
            otp_hash, _exp, _member = t._send_audience_otp("sms", "5145551234")
        self.assertIsNone(otp_hash)
        self.assertFalse(mailed.called)

    def test_a_refused_delivery_does_not_consume_the_budget(self):
        """Sinon un opérateur SMS en panne verrouillerait l'accès du visiteur."""
        t = self._open_transfer()
        with patch(SMS_MOD + ".configured", return_value=True), \
                patch(SMS_MOD + ".send", return_value=False):
            t._send_audience_otp("sms", "5145551234")
        member = t._audience_for("sms", "5145551234")
        self.assertEqual(member.otp_send_count, 0)

    # ------------------------------------------------------------------ confirmation
    def test_confirmation_registers_the_visitor_and_logs_it(self):
        t = self._open_transfer()
        member = t._audience_join("email", "visiteur@example.com")
        t._audience_confirm(member, ip="203.0.113.5")
        self.assertEqual(member.state, "confirmed")
        self.assertTrue(member.confirmed_at)
        self.assertEqual(t.audience_count, 1)
        actions = t.access_log_ids.mapped("action")
        self.assertIn("audience_requested", actions)
        self.assertIn("audience_joined", actions)

    def test_confirmation_notifies_the_sender_once(self):
        """C'est le seul avis qui parte à CHAQUE visiteur : l'avis de
        téléchargement, lui, ne se déclenche qu'une fois par transfert."""
        t = self._open_transfer()
        member = t._audience_join("email", "visiteur@example.com")
        with patch.object(type(t), "_notify_audience_join") as notified:
            t._audience_confirm(member)
            t._audience_confirm(member)
        self.assertEqual(notified.call_count, 1)

    def test_notification_is_skipped_when_the_sender_opted_out(self):
        t = self._open_transfer(notify_on_join=False)
        member = t._audience_join("email", "visiteur@example.com")
        with patch.object(type(t), "_notify_audience_join") as notified:
            t._audience_confirm(member)
        self.assertFalse(notified.called)

    # ------------------------------------------------------------------ filigrane
    def test_watermark_names_the_confirmed_visitor(self):
        """Avant l'audience ouverte, un transfert à plusieurs destinataires ne
        pouvait estamper qu'une IP. L'identité confirmée est meilleure : elle
        est prouvée et elle est individuelle."""
        t = self._active_open()
        joined = " ".join(t._watermark_lines(
            t.file_ids, ip="203.0.113.7", actor="visiteur@example.com"))
        self.assertIn("visiteur@example.com", joined)
        self.assertNotIn("203.0.113.7", joined)

    def test_watermark_never_prints_a_full_phone_number(self):
        """Le filigrane finit sur un PDF qui circule : le transfert n'a aucune
        raison d'y publier le mobile de quelqu'un."""
        t = self._active_open()
        with patch(SMS_MOD + ".configured", return_value=True):
            member = t._audience_join("sms", "5145551234")
        self.assertTrue(member, "le visiteur mobile doit être admis ici")
        joined = " ".join(t._watermark_lines(
            t.file_ids, actor=member.display_identity))
        self.assertNotIn("5145551234", joined)
        self.assertIn("1234", joined)

    def test_watermark_falls_back_when_no_identity_is_known(self):
        """Régression du mode nommé : sans acteur, le comportement d'avant."""
        t = self._active_open()
        joined = " ".join(t._watermark_lines(t.file_ids, ip="203.0.113.7"))
        self.assertIn("203.0.113.7", joined)

    # ------------------------------------------------------------------ budget par visiteur
    def test_per_visitor_download_budget(self):
        """max_downloads est global : sur une salle de données, dix visiteurs
        l'épuiseraient pour le onzième."""
        t = self._active_open(audience_max_downloads=1)
        member = t._audience_join("email", "visiteur@example.com")
        t._audience_confirm(member)
        t._register_download(t.file_ids, "203.0.113.5", "ua", member=member)
        self.assertEqual(member.download_count, 1)
        with self.assertRaises(UserError):
            t._register_download(t.file_ids, "203.0.113.5", "ua", member=member)

    def test_one_visitor_budget_does_not_touch_another(self):
        t = self._active_open(audience_max_downloads=1)
        a = t._audience_join("email", "a@example.com")
        b = t._audience_join("email", "b@example.com")
        t._register_download(t.file_ids, "203.0.113.5", "ua", member=a)
        t._register_download(t.file_ids, "203.0.113.6", "ua", member=b)
        self.assertEqual(a.download_count, 1)
        self.assertEqual(b.download_count, 1)

    def test_global_budget_still_applies_over_the_visitors(self):
        """Le budget par visiteur s'ajoute au plafond global, il ne le
        remplace pas."""
        t = self._active_open(audience_max_downloads=5)
        t.max_downloads = 1
        a = t._audience_join("email", "a@example.com")
        b = t._audience_join("email", "b@example.com")
        t._register_download(t.file_ids, "203.0.113.5", "ua", member=a)
        with self.assertRaises(UserError):
            t._register_download(t.file_ids, "203.0.113.6", "ua", member=b)

    def test_a_blocked_visitor_cannot_download(self):
        t = self._active_open()
        member = t._audience_join("email", "visiteur@example.com")
        t._audience_confirm(member)
        member.action_block()
        with self.assertRaises(UserError):
            t._register_download(t.file_ids, "203.0.113.5", "ua", member=member)

    def test_a_blocked_visitor_is_refused_a_new_code(self):
        t = self._open_transfer()
        member = t._audience_join("email", "visiteur@example.com")
        member.action_block()
        ok, reason = t._audience_admissible("email", "visiteur@example.com")
        self.assertFalse(ok)
        self.assertEqual(reason, "blocked")

    def test_unblock_restores_a_confirmed_visitor(self):
        t = self._open_transfer()
        member = t._audience_join("email", "visiteur@example.com")
        t._audience_confirm(member)
        member.action_block()
        member.action_unblock()
        self.assertEqual(member.state, "confirmed")

    # ------------------------------------------------------------------ preuve
    def test_audience_rows_cannot_be_deleted(self):
        """L'audience dit QUI a été admis : c'est une pièce du journal."""
        t = self._open_transfer()
        member = t._audience_join("email", "visiteur@example.com")
        with self.assertRaises(UserError):
            member.unlink()

    def test_audience_rows_go_with_the_transfer_at_gc(self):
        t = self._open_transfer()
        member = t._audience_join("email", "visiteur@example.com")
        member.with_context(st_gc=True).unlink()
        self.assertFalse(member.exists())

    # ------------------------------------------------------------------ ancrage en mode nommé
    def test_declared_mode_anchors_a_confirmed_recipient(self):
        """La ligne d'audience n'admet personne en mode nommé — la liste l'a
        déjà fait. Elle ancre : c'est à elle que s'accrochent le filigrane
        nominatif, le budget par personne et la NDA du module pont."""
        t = self._transfer(recipient_emails="connu@example.com")
        member = t._audience_join("email", "connu@example.com")
        self.assertTrue(member)
        t._audience_confirm(member)
        self.assertEqual(member.state, "confirmed")

    def test_declared_mode_refuses_to_anchor_a_stranger(self):
        t = self._transfer(recipient_emails="connu@example.com")
        self.assertFalse(t._audience_join("email", "intrus@example.com"))

    def test_declared_mode_refuses_a_mobile_identity(self):
        """Le canal SMS du mode nommé livre à un numéro DÉJÀ connu pour une
        adresse connue ; il n'ouvre pas une identité par numéro."""
        t = self._transfer(recipient_emails="connu@example.com")
        ok, reason = t._audience_admissible("sms", "5145551234")
        self.assertFalse(ok)
        self.assertEqual(reason, "channel")

    def test_declared_mode_never_notifies_the_sender_on_confirmation(self):
        """L'expéditeur d'un envoi nommé SAIT à qui il a écrit : un courriel
        par destinataire serait du bruit."""
        t = self._transfer(recipient_emails="connu@example.com")
        member = t._audience_join("email", "connu@example.com")
        with patch.object(type(t), "_notify_audience_join") as notified:
            t._audience_confirm(member)
        self.assertFalse(notified.called)

    def test_declared_mode_ignores_the_visitor_cap(self):
        """Un plafond hérité de la marque ne doit pas fermer un envoi nommé à
        son onzième destinataire."""
        t = self._transfer(recipient_emails=", ".join(
            "d%s@example.com" % i for i in range(10)))
        self.brand.audience_max_default = 1
        for i in range(10):
            self.assertTrue(t._audience_join("email", "d%s@example.com" % i))
        self.brand.audience_max_default = 50

    # ------------------------------------------------------------------ surface publique
    def test_public_config_never_publishes_the_audience_policy(self):
        """Le bloc de configuration de /secrets est rendu EN CLAIR à un visiteur
        anonyme. Y laisser les réglages d'audience lui dirait si le locataire
        fait des salles de données, si le SMS est câblé et combien de visiteurs
        un lien accepte — c'est-à-dire ce qu'on voudrait savoir avant de sonder
        les plafonds. La page publique n'offre pas le mode : elle n'en a aucun
        usage. (Fuite trouvée au QA du 2026-08-21.)"""
        from odoo.addons.bf_securetransfer.controllers.main import _st_config
        limits = self.brand._effective_limits()
        self.assertIn("allow_open_audience", limits,
                      "le backend, lui, a bien besoin de la clé")
        config = _st_config(self.env, limits, "fr_CA")
        published = config["limits"]
        for leaked in ("allow_open_audience", "allow_audience_sms",
                       "audience_max_default"):
            self.assertNotIn(leaked, published,
                             "« %s » ne doit pas sortir sur la page publique" % leaked)
        # ...sans avoir amputé ce dont la page a réellement besoin
        for kept in ("max_bytes", "max_files", "expiry_choices", "allow_password"):
            self.assertIn(kept, published)

    def test_identity_normalisation_is_single_sourced(self):
        """Deux appelants qui normalisent différemment créeraient deux lignes
        pour la même personne — et deux budgets de codes."""
        Audience = self.env["secure.transfer.audience"]
        self.assertEqual(
            Audience._identity_values("email", " Jean@Example.COM "),
            ("email", "jean@example.com", False))
        self.assertEqual(
            Audience._identity_values("sms", "+1 (514) 555-1234"),
            ("sms", False, "5145551234"))
        self.assertIsNone(Audience._identity_values("sms", "12345"))
        self.assertIsNone(Audience._identity_values("email", "pas-une-adresse"))

    # ------------------------------------------------------ entrée de menu dédiée
    def test_menu_entry_opens_the_wizard_already_in_open_mode(self):
        """Le mode ne se choisissait qu'en dépliant un radio, après avoir deviné
        qu'il fallait d'abord poser la bonne marque. L'entrée de menu doit
        livrer un formulaire déjà en audience ouverte."""
        res = self.env["secure.transfer.send.wizard"].with_context(
            st_open_audience=True,
        ).default_get(["brand_id", "audience_mode"])
        self.assertEqual(res.get("audience_mode"), "open")
        self.assertEqual(res.get("brand_id"), self.brand.id)

    def test_menu_entry_survives_the_brand_onchange(self):
        """⚠ Le vrai piège : `default_audience_mode` posé dans le contexte de
        l'action serait ramené à « destinataires nommés » par l'onchange au
        chargement du formulaire, parce qu'une marque vide n'offre rien. C'est
        le choix d'une marque QUI OFFRE le mode qui fait tenir le défaut."""
        Wizard = self.env["secure.transfer.send.wizard"]
        wiz = Wizard.with_context(st_open_audience=True).create(
            Wizard.with_context(st_open_audience=True).default_get(
                ["brand_id", "audience_mode"]))
        wiz._onchange_brand_audience()
        self.assertEqual(wiz.audience_mode, "open")
        self.assertTrue(wiz.brand_allows_audience)

    def test_context_alone_would_not_have_held(self):
        """Contre-épreuve : la raison d'être du drapeau. Sans marque, l'onchange
        annule le mode — poser le seul `default_audience_mode` était un défaut
        qui s'évaporait en silence."""
        wiz = self.env["secure.transfer.send.wizard"].new({
            "brand_id": False, "audience_mode": "open"})
        wiz._onchange_brand_audience()
        self.assertEqual(wiz.audience_mode, "declared")

    def test_menu_entry_does_not_touch_the_ordinary_composer(self):
        """Sans le drapeau, rien ne change : tout le trafic existant passe par
        l'entrée « Nouvel envoi sécurisé »."""
        res = self.env["secure.transfer.send.wizard"].default_get(
            ["brand_id", "audience_mode"])
        self.assertEqual(res.get("audience_mode"), "declared")

    def test_menu_entry_keeps_an_explicitly_requested_eligible_brand(self):
        """L'entrée répare un défaut, elle ne dicte pas un choix déjà posé."""
        other = self.env["secure.transfer.brand"].create({
            "name": "Marque audience bis",
            "slug": "audience-bis-menu",
            "allow_open_audience": True,
            "audience_max_default": 10,
        })
        res = self.env["secure.transfer.send.wizard"].with_context(
            st_open_audience=True, default_brand_id=other.id,
        ).default_get(["brand_id", "audience_mode"])
        self.assertEqual(res.get("brand_id"), other.id)

    def test_menu_entry_refuses_rather_than_falling_back_silently(self):
        """Aucune marque n'offre le mode : ouvrir un formulaire muet en
        « destinataires nommés » ferait croire que la fonction n'existe pas.
        L'erreur dit où l'activer."""
        self.env["secure.transfer.brand"].search([]).write(
            {"allow_open_audience": False})
        with self.assertRaises(UserError):
            self.env["secure.transfer.send.wizard"].with_context(
                st_open_audience=True).default_get(["brand_id", "audience_mode"])

    def test_menu_action_carries_the_flag(self):
        """Le défaut vit dans le contexte de l'action : quelqu'un qui le
        retirerait rendrait le menu identique à l'autre, sans rien casser
        d'autre — donc sans que rien ne le signale."""
        action = self.env.ref(
            "bf_securetransfer.action_secure_send_open_audience")
        self.assertIn("st_open_audience", action.context or "")
        menu = self.env.ref("bf_securetransfer.menu_secure_send_open_audience")
        self.assertEqual(menu.action.id, action.id)
