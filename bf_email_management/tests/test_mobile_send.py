"""Envois : destinataires calculés, anti-doublon, corps et pièces."""
from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import MobileApiCase


@tagged("post_install", "-at_install")
class TestMobileSend(MobileApiCase):

    def _messages_on(self, model, res_id):
        return self.env["mail.message"].sudo().search([
            ("model", "=", model), ("res_id", "=", res_id),
        ])

    # ------------------------------------------------------- destinataires
    def test_reply_targets_the_original_sender(self):
        record = self.as_owner().browse(self.inbound.id)
        to_ids = record._build_reply_recipients()
        emails = self.env["res.partner"].sudo().browse(to_ids).mapped("email")
        self.assertEqual(emails, ["client@acme.test"])

    def test_reply_all_excludes_your_own_address(self):
        """Se répondre à soi-même à chaque « répondre à tous » est le défaut
        classique de cette fonction."""
        record = self.as_owner().browse(self.inbound.id)
        to_ids, cc_ids = record._build_reply_all_recipients()
        everyone = self.env["res.partner"].sudo().browse(to_ids + cc_ids)
        self.assertNotIn("owner@test.invalid", everyone.mapped("email"))

    def test_forward_demands_a_recipient(self):
        with self.assertRaises(UserError):
            self.as_owner().browse(self.inbound.id).mobile_reply(
                mode="forward", body="Pour information.")

    def test_empty_body_is_refused(self):
        with self.assertRaises(UserError):
            self.as_owner().browse(self.inbound.id).mobile_reply(
                mode="reply", body="   ")

    def test_unknown_mode_is_refused(self):
        with self.assertRaises(UserError):
            self.as_owner().browse(self.inbound.id).mobile_reply(
                mode="teleporter", body="Bonjour.")

    # ------------------------------------------------------------- contenu
    def test_reply_body_is_escaped_and_quoted(self):
        record = self.as_owner().browse(self.inbound.id)
        before = self._messages_on(*record._composer_target())
        record.mobile_reply(mode="reply", body="Merci <script>alert(1)</script>")
        posted = (self._messages_on(*record._composer_target()) - before)
        self.assertTrue(posted)
        body = posted[0].body
        # Le texte tapé est du texte, pas du balisage.
        self.assertNotIn("<script>", body)
        # Et l'original est cité.
        self.assertIn("blockquote", body)

    def test_reply_flips_an_inbound_message_to_replied(self):
        self.as_owner().browse(self.inbound.id).mobile_reply(
            mode="reply", body="Confirmé.")
        self.assertEqual(self.inbound.status, "replied")

    def test_forward_does_not_flip_the_status(self):
        """Transférer n'est pas répondre : le correspondant attend toujours."""
        self.inbound.status = "read"
        self.as_owner().browse(self.inbound.id).mobile_reply(
            mode="forward", body="Pour info.", to=["collegue@test.invalid"])
        self.assertEqual(self.inbound.status, "read")

    # -------------------------------------------------------- anti-doublon
    def test_the_same_client_token_sends_only_once(self):
        """Le cas dangereux n'est pas l'échec, c'est le succès dont la réponse
        s'est perdue : le téléphone rejoue, et le correspondant reçoit deux
        fois le même message."""
        record = self.as_owner().browse(self.inbound.id)
        model, res_id = record._composer_target()
        before = self._messages_on(model, res_id)

        first = record.mobile_reply(mode="reply", body="Une seule fois.",
                                    client_token="jeton-fixe")
        second = record.mobile_reply(mode="reply", body="Une seule fois.",
                                     client_token="jeton-fixe")
        third = record.mobile_reply(mode="reply", body="Une seule fois.",
                                    client_token="jeton-fixe")

        self.assertFalse(first.get("duplicate"))
        self.assertTrue(second.get("duplicate"))
        self.assertTrue(third.get("duplicate"))

        posted = self._messages_on(model, res_id) - before
        self.assertEqual(len(posted), 1,
                         "trois requêtes au même jeton ont produit %d messages"
                         % len(posted))

    def test_a_different_token_still_sends(self):
        record = self.as_owner().browse(self.inbound.id)
        model, res_id = record._composer_target()
        before = self._messages_on(model, res_id)
        record.mobile_reply(mode="reply", body="A", client_token="jeton-a")
        record.mobile_reply(mode="reply", body="B", client_token="jeton-b")
        self.assertEqual(len(self._messages_on(model, res_id) - before), 2)

    def test_dedup_is_opt_in(self):
        """Sans jeton, aucun rapprochement : deux envois, deux messages."""
        record = self.as_owner().browse(self.inbound.id)
        model, res_id = record._composer_target()
        before = self._messages_on(model, res_id)
        record.mobile_reply(mode="reply", body="Un.")
        record.mobile_reply(mode="reply", body="Deux.")
        self.assertEqual(len(self._messages_on(model, res_id) - before), 2)

    def test_send_ledger_is_swept(self):
        Ledger = self.env["bf.email.mobile.send"]
        Ledger._claim("vieux-jeton")
        self.assertGreaterEqual(Ledger._gc(days=0), 1)
        # Purgé ⇒ le même jeton redevient utilisable, ce qui est voulu :
        # 30 jours après, ce n'est plus un rejeu, c'est un nouvel envoi.
        self.assertTrue(Ledger._claim("vieux-jeton"))

    # ------------------------------------------------------------- compose
    def test_compose_files_on_the_contact_card_when_allowed(self):
        """Un usager qui PEUT écrire sur les contacts garde le bon classement."""
        self.owner.write({
            "groups_id": [(4, self.env.ref("base.group_partner_manager").id)],
        })
        self.as_owner().mobile_compose(
            to=["client@acme.test"], subject="Nouveau", body="Bonjour.")
        posted = self._messages_on("res.partner", self.partner.id)
        self.assertTrue(posted, "le message doit atterrir sur la fiche du destinataire")

    def test_compose_works_for_a_plain_internal_user(self):
        """Régression : un interne simple n'a PAS ``write`` sur ``res.partner``.

        La fiche contact est le bon foyer, mais y poster l'exige ; « composer »
        marchait donc pour un gestionnaire de contacts et tombait en AccessError
        pour tout le monde d'autre. Toutes les sondes de banc tournaient en
        admin, d'où l'angle mort.
        """
        Partner = self.env["res.partner"].with_user(self.owner)
        self.assertFalse(
            Partner.check_access_rights("write", raise_exception=False),
            "prémisse du test : le propriétaire n'est pas gestionnaire de contacts")

        result = self.as_owner().mobile_compose(
            to=["client@acme.test"], subject="Depuis le téléphone",
            body="Bonjour.")
        self.assertTrue(result["ok"])

    def test_compose_falls_back_to_an_owned_row_not_the_contact_card(self):
        """Le repli doit rester dans ce que l'usager possède."""
        before = self.as_owner().search_count([("direction", "=", "out")])
        self.as_owner().mobile_compose(
            to=["client@acme.test"], subject="X", body="Y")
        after = self.as_owner().search_count([("direction", "=", "out")])
        self.assertEqual(after, before + 1,
                         "une ligne bf.email sortante devait accueillir le message")

    def test_compose_onto_a_forbidden_model_is_refused(self):
        with self.assertRaises(UserError):
            self.as_owner().mobile_compose(
                to=["client@acme.test"], subject="X", body="Y",
                res_model="ir.config_parameter", res_id=1)

    def test_compose_without_a_resolvable_recipient_is_refused(self):
        with self.assertRaises(UserError):
            self.as_owner().mobile_compose(to=[], subject="X", body="Y")


@tagged("post_install", "-at_install")
class TestMobileContacts(MobileApiCase):
    """Complétion du carnet d'adresses pour les champs À / Cc."""

    def test_a_contact_without_an_email_is_never_offered(self):
        """Proposer un nom auquel on ne peut pas écrire est pire que rien."""
        self.env["res.partner"].create({"name": "Sans Adresse Zzz"})
        found = self.as_owner().mobile_search_contacts("Sans Adresse")
        self.assertEqual(found["contacts"], [])

    def test_search_matches_name_or_address(self):
        by_name = self.as_owner().mobile_search_contacts("Acme")
        by_email = self.as_owner().mobile_search_contacts("client@")
        self.assertTrue(by_name["contacts"])
        self.assertEqual(by_name["contacts"][0]["email"], "client@acme.test")
        self.assertEqual(by_email["contacts"][0]["email"], "client@acme.test")

    def test_a_short_term_returns_nothing(self):
        """Deux caractères minimum : sinon la première frappe balaie la base."""
        self.assertEqual(self.as_owner().mobile_search_contacts("a")["contacts"], [])
        self.assertEqual(self.as_owner().mobile_search_contacts("")["contacts"], [])

    def test_the_result_set_is_bounded(self):
        Partner = self.env["res.partner"]
        for i in range(40):
            Partner.create({"name": "Foule %d" % i, "email": "foule%d@test.invalid" % i})
        found = self.as_owner().mobile_search_contacts("Foule", limit=999)
        self.assertLessEqual(len(found["contacts"]), 30)


@tagged("post_install", "-at_install")
class TestMobileRichText(MobileApiCase):
    """Corps en texte enrichi : filtré, pas échappé — et jamais exécutable."""

    def _posted_body(self, record):
        return self.env["mail.message"].sudo().search(
            [("model", "=", record._composer_target()[0]),
             ("res_id", "=", record._composer_target()[1])],
            order="id desc", limit=1,
        ).body

    def test_plain_text_is_escaped_not_interpreted(self):
        """« <b>gras</b> » tapé au clavier doit rester ces caractères-là."""
        record = self.as_owner().browse(self.inbound.id)
        record.mobile_reply(mode="reply", body="Dis <b>bonjour</b>")
        body = self._posted_body(record)
        self.assertIn("&lt;b&gt;", body)

    def test_rich_text_keeps_its_formatting(self):
        record = self.as_owner().browse(self.inbound.id)
        record.mobile_reply(mode="reply",
                            body="<p>Un <strong>point</strong> important</p>",
                            body_is_html=True)
        body = self._posted_body(record)
        self.assertIn("<strong>", body)

    def test_rich_text_is_sanitized(self):
        """Le téléphone n'est pas une source de confiance : le corps part par
        courriel et atterrit dans un chatter."""
        record = self.as_owner().browse(self.inbound.id)
        record.mobile_reply(
            mode="reply",
            body='<p>Bonjour</p><script>alert(1)</script>'
                 '<img src=x onerror="alert(2)">',
            body_is_html=True,
        )
        body = self._posted_body(record)
        self.assertNotIn("<script", body.lower())
        self.assertNotIn("onerror", body.lower())
        self.assertIn("Bonjour", body)

    def test_a_body_that_is_only_markup_is_refused(self):
        record = self.as_owner().browse(self.inbound.id)
        with self.assertRaises(UserError):
            record.mobile_reply(mode="reply", body="<script>alert(1)</script>",
                                body_is_html=True)
