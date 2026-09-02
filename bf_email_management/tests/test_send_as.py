"""Écrire sous une autre de ses adresses, sans devenir quelqu'un d'autre.

Ce que ces tests tiennent, dans l'ordre où ça compte :

1. le « De » sort réellement sous l'identité choisie, et l'auteur reste la
   personne qui écrit ;
2. sans identité, rien ne change — la surcharge ne doit pas déplacer le « De »
   de tous les composeurs de l'instance ;
3. on ne peut pas porter l'identité d'autrui, ni une identité que personne
   n'a vérifiée ;
4. la signature n'entre JAMAIS dans le corps, elle est posée une seule fois
   à l'envoi, et elle suit l'identité qui expédie.

⚠️ Le point 1 s'est révélé faux à la première écriture, et aucun test de
modèle ne l'aurait vu : ``_prepare_mail_values`` fusionne
``dict(base_values, **additional)``, et ``_prepare_mail_values_rendered``
réécrivait par-dessus le ``email_from`` posé côté statique. Le test part donc
du composeur et va lire le ``mail.message`` produit, plutôt que de vérifier
le dictionnaire à mi-chemin.
"""

from odoo import Command, fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class SendAsCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Users = cls.env["res.users"].with_context(no_reset_password=True)
        group_user = cls.env.ref("base.group_user").id

        # ⚠️ base.group_partner_manager n'est pas décoratif : sans lui, un
        # usager interne ne peut pas créer de mail.message sur un res.partner
        # (AccessError « Document type: Message, Operation: create »), et les
        # tests échoueraient AVEC comme SANS identité — donc sans rien dire de
        # la fonction éprouvée ici.
        cls.writer = Users.create({
            "name": "Jane Doe",
            "login": "sendas.writer@test.invalid",
            "email": "jane@societe-a.test",
            "signature": "<p>-- <br/>Jane, Société A</p>",
            "groups_id": [(6, 0, [
                group_user,
                cls.env.ref("base.group_partner_manager").id,
            ])],
        })
        cls.stranger = Users.create({
            "name": "Quelqu'un d'autre",
            "login": "sendas.stranger@test.invalid",
            "email": "autre@societe-a.test",
            "groups_id": [(6, 0, [group_user])],
        })

        Identity = cls.env["bf.email.identity"].sudo()
        cls.primary = Identity.create({
            "user_id": cls.writer.id,
            "name": "Jane Doe",
            "email": "jane@societe-a.test",
            "verified": True,
            "is_default": True,
        })
        cls.second = Identity.create({
            "user_id": cls.writer.id,
            "name": "Jane Doe",
            "email": "jane@societe-b.test",
            "signature_html": "<p>-- <br/>Jane, Société B</p>",
            "verified": True,
        })
        cls.unverified = Identity.create({
            "user_id": cls.writer.id,
            "name": "Jane Doe",
            "email": "jane@pas-a-moi.test",
            "verified": False,
        })
        cls.foreign = Identity.create({
            "user_id": cls.stranger.id,
            "name": "Quelqu'un d'autre",
            "email": "autre@societe-a.test",
            "verified": True,
        })

        # ⚠️ ``res.users.create`` re-rend la signature depuis le gabarit de
        # la société (bf_multi_company_email) : celle passée aux valeurs de
        # création ne survit pas, et les assertions ci-dessous ne mesureraient
        # plus rien. Un write sur le SEUL champ ``signature`` ne déclenche pas
        # ce rendu — il n'est pas dans ``_BF_SIGNATURE_USER_TRIGGERS``.
        cls.writer.sudo().write({"signature": "<p>-- <br/>Jane, Société A</p>"})
        assert "Société A" in (cls.writer.signature or ""), (
            "la signature de test n'a pas tenu : les tests de signature "
            "passeraient sans rien éprouver")

        cls.recipient = cls.env["res.partner"].create({
            "name": "Destinataire",
            "email": "dest@ailleurs.test",
        })
        cls.carrier = cls.env["res.partner"].create({
            "name": "Fiche porteuse",
            "email": "porteuse@ailleurs.test",
        })

    # ------------------------------------------------------------------
    # Outils
    # ------------------------------------------------------------------
    def _post(self, identity=None, user=None):
        """Poster par le composeur et rendre le mail.message effectivement créé."""
        env = self.env(user=(user or self.writer).id)
        composer = env["mail.compose.message"].create({
            "model": "res.partner",
            "res_ids": repr([self.carrier.id]),
            "composition_mode": "comment",
            "subject": "Essai",
            "body": "<p>Bonjour</p>",
            "partner_ids": [Command.set([self.recipient.id])],
            "bf_identity_id": identity.id if identity else False,
        })
        high_water = env["mail.message"].search([], order="id desc", limit=1).id
        composer._action_send_mail()
        return env["mail.message"].search(
            [("id", ">", high_water)], order="id desc", limit=1)

    # ------------------------------------------------------------------
    # 1. Le « De » suit l'identité
    # ------------------------------------------------------------------
    def test_the_from_carries_the_chosen_identity(self):
        message = self._post(self.second)
        self.assertIn("jane@societe-b.test", message.email_from)

    def test_the_author_stays_the_person_writing(self):
        """Porter une autre adresse n'est pas se faire passer pour un autre.

        ``_message_compute_author`` retourne les deux inchangés quand les deux
        sont fournis : c'est ce qui permet au « De » de bouger pendant que la
        traçabilité interne, elle, ne bouge pas.
        """
        message = self._post(self.second)
        self.assertEqual(message.author_id, self.writer.partner_id)

    def test_the_outgoing_server_follows_the_identity(self):
        server = self.env["ir.mail_server"].sudo().create({
            "name": "Société B",
            "smtp_host": "smtp.societe-b.test",
            "from_filter": "societe-b.test",
        })
        self.second.sudo().mail_server_id = server
        message = self._post(self.second)
        self.assertEqual(message.mail_server_id, server)

    # ------------------------------------------------------------------
    # 2. Sans identité, rien ne bouge
    # ------------------------------------------------------------------
    def test_without_an_identity_the_from_is_untouched(self):
        """La surcharge ne doit pas déplacer le « De » de toute l'instance."""
        message = self._post(None)
        self.assertIn("jane@societe-a.test", message.email_from)

    def test_without_an_identity_no_server_is_forced(self):
        message = self._post(None)
        self.assertFalse(message.mail_server_id)

    # ------------------------------------------------------------------
    # 3. Ce qu'on n'a pas le droit de porter ne part pas
    # ------------------------------------------------------------------
    def test_another_persons_identity_is_refused(self):
        """Le domaine du champ borne l'écran ; un appel RPC ne passe pas par là."""
        with self.assertRaises(UserError):
            self._post(self.foreign)

    def test_an_unverified_identity_is_refused(self):
        with self.assertRaises(UserError):
            self._post(self.unverified)

    def test_an_archived_identity_is_refused(self):
        self.second.sudo().active = False
        with self.assertRaises(UserError):
            self._post(self.second)

    def test_a_plain_user_cannot_verify_his_own_identity(self):
        """Se vérifier soi-même viderait la garde de son sens."""
        identity = self.env["bf.email.identity"].with_user(self.writer).create({
            "user_id": self.writer.id,
            "name": "Jane",
            "email": "jane@invente.test",
        })
        self.assertFalse(identity.verified)
        with self.assertRaises(ValidationError):
            identity.with_user(self.writer).verified = True

    def test_an_email_admin_can_verify(self):
        admin_group = self.env.ref("bf_email_management.group_email_admin")
        self.writer.sudo().groups_id = [Command.link(admin_group.id)]
        identity = self.env["bf.email.identity"].with_user(self.writer).create({
            "user_id": self.writer.id,
            "name": "Jane",
            "email": "jane@societe-c.test",
        })
        identity.with_user(self.writer).verified = True
        self.assertTrue(identity.verified)

    def test_a_person_never_sees_another_persons_identities(self):
        visible = self.env["bf.email.identity"].with_user(self.writer).search([])
        self.assertNotIn(self.foreign, visible)

    # ------------------------------------------------------------------
    # 4. La signature quitte le corps et se pose à l'envoi
    # ------------------------------------------------------------------
    # ⚠️ Ces tests remplacent ceux qui éprouvaient la substitution de
    # signature DANS le corps du composeur. Elle n'existe plus : un corps qui
    # porte la signature la fait sortir deux fois, puisque le gabarit de
    # notification l'ajoute de toute façon au rendu. Mesuré le 2026-08-31 sur
    # un fil réel : neuf blocs dans le corps, dix dans le courriel rendu.
    def _visible(self, rendu):
        """Le courriel sans l'aperçu texte caché que le gabarit met en tête.

        ⚠️ ``mail.notification_preview`` recopie le TEXTE du corps dans un div
        invisible destiné à la ligne d'aperçu des clients de messagerie.
        Compter une signature sur le rendu brut la compte donc DEUX fois dès
        que le corps la porte — un faux positif qui a fait échouer cet essai
        avant qu'on regarde le gabarit.
        """
        marque = 'style="display: none; max-height: 0px;'
        debut = rendu.find(marque)
        if debut < 0:
            return rendu
        fin = rendu.find("</div>", debut)
        return rendu[:debut] + rendu[fin + 6:] if fin >= 0 else rendu

    def _placement(self, mode):
        """Poser le réglage « Où vit la signature » pour cet essai.

        ⚠️ Aucun essai de signature ne doit dépendre du défaut : le défaut a
        déjà changé deux fois en une journée, et un essai muet aurait suivi
        sans rien dire.
        """
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_email.signature_placement", mode)
        self.assertEqual(self.env["bf.email"]._signature_placement(), mode)

    def _row(self, **extra):
        """Une rangée bf.email orpheline, prête pour Répondre / Transférer."""
        vals = {
            "subject": "Question",
            "direction": "in",
            "status": "new",
            "email_from": "client@ailleurs.test",
            "email_to": "jane@societe-a.test",
            "body_html": "<p>Le message d'origine</p>",
            "date": fields.Datetime.now(),
            "user_id": self.writer.id,
        }
        vals.update(extra)
        return self.env["bf.email"].with_user(self.writer).create(vals)

    def _rendered_email(self, message, record):
        """Le corps HTML tel que le destinataire le recevra.

        Rejoue le chemin de production plutôt qu'un raccourci : c'est la seule
        façon de compter les signatures là où elles comptent — dans le
        courriel, pas dans le champ ``body``.
        """
        values = record._notify_by_email_prepare_rendering_context(
            message, msg_vals={})
        recipients = [
            group for group in record._notify_get_recipients(message, {})
            if group.get("notif") == "email"
        ]
        return record._notify_by_email_render_layout(
            message,
            {"recipients": recipients[:1], "has_button_access": False,
             "button_access": {}, "actions": []},
            msg_vals={}, render_values=values)

    def test_a_new_message_opens_on_a_body_without_signature(self):
        self._placement("envoi")
        action = self.env["bf.email"].with_user(self.writer).inbox_compose()
        body = action["context"]["default_body"]
        self.assertNotIn("Société A", body)
        self.assertNotIn("Société B", body)
        # La ligne d'atterrissage, elle, reste : sans elle le curseur tombe
        # dans la citation et on écrit dans le texte de quelqu'un d'autre.
        self.assertIn("<br/>", body)

    def test_a_reply_quote_carries_no_signature(self):
        self._placement("envoi")
        quote = self._row()._build_reply_quote_body()
        self.assertNotIn("Société A", quote)
        self.assertIn("Le message d'origine", quote)

    def test_a_forward_carries_no_signature(self):
        self._placement("envoi")
        forwarded = self._row()._build_forward_body()
        self.assertNotIn("Société A", forwarded)
        self.assertIn("Forwarded message", forwarded)

    def test_the_mobile_app_is_handed_no_signature(self):
        self._placement("envoi")
        config = self.env["bf.email"].with_user(self.writer).get_mobile_config()
        self.assertEqual(config["signature"], "")

    def test_the_sent_email_carries_exactly_one_signature(self):
        message = self._post(self.primary)
        self.assertNotIn("Société A", message.body)
        rendered = self._rendered_email(message, self.carrier)
        self.assertEqual(rendered.count("Société A"), 1)

    def test_the_sent_email_signs_with_the_identity_that_sends(self):
        message = self._post(self.second)
        rendered = self._rendered_email(message, self.carrier)
        self.assertEqual(rendered.count("Société B"), 1)
        self.assertNotIn("Société A", rendered)

    def test_an_identity_without_signature_falls_back_to_the_user(self):
        self.assertIn("Société A", self.primary._signature_for())
        message = self._post(self.primary)
        self.assertIn("Société A", self._rendered_email(message, self.carrier))

    def test_an_address_that_is_not_a_verified_identity_signs_nothing_special(self):
        """Signer d'une identité qu'on n'a pas le droit de porter serait pire
        que ne pas signer."""
        Identity = self.env["bf.email.identity"].sudo()
        self.assertFalse(Identity._for_sender("jane@pas-a-moi.test", self.writer))
        self.assertFalse(Identity._for_sender("autre@societe-a.test", self.writer))
        self.assertEqual(
            Identity._for_sender("Jane <JANE@societe-b.test>", self.writer),
            self.second)

    # ------------------------------------------------------------------
    # 4 bis. L'aperçu : voir la signature sans l'écrire dans le corps
    # ------------------------------------------------------------------
    def _composer(self, **valeurs):
        base = {
            "model": "res.partner",
            "res_ids": repr([self.carrier.id]),
            "composition_mode": "comment",
            "subject": "Essai",
            "body": "<p>Bonjour</p>",
        }
        base.update(valeurs)
        return self.env(user=self.writer.id)["mail.compose.message"].create(base)

    def test_the_preview_shows_the_signature_that_will_be_appended(self):
        self._placement("envoi")
        self.assertIn("Société A", self._composer().bf_signature_preview or "")

    def test_the_preview_follows_the_sending_identity(self):
        self._placement("envoi")
        preview = self._composer(
            bf_identity_id=self.second.id).bf_signature_preview or ""
        self.assertIn("Société B", preview)
        self.assertNotIn("Société A", preview)

    def test_the_preview_drops_the_signature_when_the_send_would(self):
        """Un aperçu qui montre ce qui ne partira pas ment — et un aperçu qui
        ment est pire que pas d'aperçu."""
        self._placement("envoi")
        composer = self._composer()
        composer.email_add_signature = False
        self.assertNotIn("Société A", composer.bf_signature_preview or "")

    def test_the_preview_shows_the_company_fallback_when_nothing_else_signs(self):
        self._placement("envoi")
        if "brand_email_signature_default" not in self.env["res.company"]._fields:
            self.skipTest("bluefox_branding n'est pas installé sur ce locataire")
        self.env.company.sudo().brand_email_signature_default = "<p>Repli société</p>"
        composer = self._composer()
        composer.email_add_signature = False
        self.assertIn("Repli société", composer.bf_signature_preview or "")

    def test_the_preview_never_enters_the_body(self):
        self._placement("envoi")
        composer = self._composer()
        self.assertNotIn("Société A", composer.body or "")
        self.assertNotIn("Société A", self._post(self.primary).body)

    # ------------------------------------------------------------------
    # 4 ter. La place de la signature : au-dessus de la citation
    # ------------------------------------------------------------------
    def _post_body(self, corps):
        """Poster ce corps exact et rendre le courriel tel qu'il partira."""
        env = self.env(user=self.writer.id)
        composer = env["mail.compose.message"].create({
            "model": "res.partner",
            "res_ids": repr([self.carrier.id]),
            "composition_mode": "comment",
            "subject": "Essai",
            "body": corps,
            "partner_ids": [Command.set([self.recipient.id])],
        })
        high_water = env["mail.message"].search([], order="id desc", limit=1).id
        composer.action_send_mail()
        message = env["mail.message"].search(
            [("id", ">", high_water), ("subject", "=", "Essai")],
            order="id desc", limit=1)
        return self._rendered_email(message, self.carrier)

    def test_the_signature_sits_above_the_quote(self):
        rendu = self._post_body(
            '<p>Ma réponse</p><blockquote>Le message d\'origine</blockquote>')
        self.assertEqual(rendu.count("Société A"), 1,
                         "une seule signature, toujours")
        self.assertLess(rendu.index("Société A"), rendu.index("<blockquote"),
                        "la signature doit passer AU-DESSUS du bloc cité")
        # 🔴 Le bloc inséré doit être du HTML, pas du texte échappé : la
        # première écriture le passait en ``str`` à ``Markup.replace``, qui
        # l'échappait, et le destinataire recevait `&lt;div style=…` en clair.
        self.assertNotIn("&lt;div style=", rendu)

    def test_the_signature_sits_above_a_forwarded_header(self):
        rendu = self._post_body(
            '<p>Je te transfère</p><p>---------- Forwarded message ----------</p>'
            '<p>De : quelqu\'un</p>')
        self.assertEqual(rendu.count("Société A"), 1)
        # ⚠️ ``rindex`` : le gabarit place en tête un aperçu TEXTE du corps,
        # où le marqueur apparaît aussi. Mesurer la première occurrence ferait
        # échouer un placement correct.
        self.assertLess(rendu.index("Société A"),
                        rendu.rindex("---------- Forwarded message"))

    def test_without_a_quote_the_signature_stays_where_odoo_puts_it(self):
        """Un courriel neuf n'a rien au-dessus de quoi passer."""
        rendu = self._post_body("<p>Bonjour, ceci est un courriel neuf.</p>")
        self.assertEqual(rendu.count("Société A"), 1)
        self.assertLess(rendu.index("courriel neuf"), rendu.index("Société A"))

    def test_a_body_that_is_only_a_quote_keeps_the_signature_at_the_end(self):
        """Personne n'a écrit au-dessus : la signature en tête serait absurde."""
        rendu = self._post_body("<blockquote>Rien que la citation</blockquote>")
        self.assertEqual(rendu.count("Société A"), 1)
        self.assertLess(rendu.index("<blockquote"), rendu.index("Société A"))

    def test_the_deepest_quote_is_not_the_anchor(self):
        """Dans un fil empilé, la signature passe au-dessus de TOUT le cité."""
        rendu = self._post_body(
            '<p>Ma réponse</p><blockquote>premier niveau'
            '<blockquote>second niveau</blockquote></blockquote>')
        self.assertEqual(rendu.count("Société A"), 1)
        self.assertLess(rendu.index("Société A"), rendu.rindex("premier niveau"))

    # ------------------------------------------------------------------
    # 4 quater. Le mode « brouillon » — le défaut depuis le 2026-08-31
    # ------------------------------------------------------------------
    def test_the_draft_opens_with_the_signature(self):
        """Ce qu'on voit en écrivant doit être ce qui part."""
        self._placement("brouillon")
        body = self.env["bf.email"].with_user(self.writer).inbox_compose()[
            "context"]["default_body"]
        self.assertIn("Société A", body)
        self.assertIn("o_bf_signature", body)
        # La ligne d'atterrissage reste au-dessus : sans elle le curseur tombe
        # dans la citation.
        self.assertLess(body.index("<br/>"), body.index("Société A"))

    def test_a_reply_quote_carries_the_signature_above_the_quote(self):
        self._placement("brouillon")
        quote = self._row().with_user(self.writer)._build_reply_quote_body()
        self.assertIn("Société A", quote)
        self.assertLess(quote.index("Société A"), quote.index("<blockquote"))

    def test_a_forward_carries_the_signature(self):
        self._placement("brouillon")
        forwarded = self._row().with_user(self.writer)._build_forward_body()
        self.assertIn("Société A", forwarded)
        self.assertLess(forwarded.index("Société A"),
                        forwarded.index("Forwarded message"))

    def test_the_mobile_app_is_handed_the_signature(self):
        self._placement("brouillon")
        config = self.env["bf.email"].with_user(self.writer).get_mobile_config()
        self.assertIn("Société A", config["signature"])

    def test_a_body_that_already_signs_gets_no_second_signature(self):
        """🔴 La garde qui tient quel que soit le chemin d'envoi.

        C'est le défaut qui a coûté la refonte du 2026-08-31 : le corps
        portait la signature ET le gabarit en ajoutait une. Mesuré alors sur
        le fil « mot de passe » d'Écolaction, neuf blocs dans le corps et dix
        dans le courriel rendu.
        """
        self._placement("brouillon")
        marque = self.env(user=self.writer.id)["bf.email"]._compose_signature_block_for_user()
        rendu = self._visible(self._post_body("<p>Ma réponse</p>%s" % marque))
        self.assertEqual(rendu.count("Société A"), 1)

    def test_the_marker_survives_the_stored_message(self):
        """🔴 Un marqueur qui ne survit pas au stockage = garde morte.

        ``mail.message.body`` est assaini à l'écriture et retire les `data-*`
        qu'Odoo ne connaît pas. Éprouver le marqueur sur un vrai
        ``mail.message``, jamais sur ``tools.html_sanitize`` appelé à la main :
        celui-ci les conserve et le contrôle passerait à côté.
        """
        self._placement("brouillon")
        marque = self.env(user=self.writer.id)["bf.email"]._compose_signature_block_for_user()
        message = self.env(user=self.writer.id)["mail.message"].create({
            "model": "res.partner", "res_id": self.carrier.id,
            "message_type": "comment",
            "subtype_id": self.env.ref("mail.mt_comment").id,
            "subject": "Marqueur", "body": "<p>Bonjour</p>%s" % marque,
        })
        self.assertIn(self.env["bf.email"].SIGNATURE_MARKER, message.body)
        valeurs = self.carrier._notify_by_email_prepare_rendering_context(message)
        self.assertFalse(valeurs.get("email_add_signature"),
                         "le gabarit ne doit pas ajouter une deuxième signature")

    def test_a_body_without_the_marker_still_gets_signed(self):
        """Un message posté ailleurs (chatter nu, automatisation) reste signé."""
        self._placement("brouillon")
        rendu = self._visible(self._post_body("<p>Un mot sans signature</p>"))
        self.assertEqual(rendu.count("Société A"), 1)

    def test_the_preview_hides_when_the_body_carries_the_signature(self):
        """Deux signatures à l'écran diraient une chose fausse."""
        self._placement("brouillon")
        self.assertFalse(self._composer().bf_signature_preview)

    def test_the_placement_setting_drives_the_draft(self):
        """Le réglage décide, et il décide dans les deux sens."""
        Boite = self.env["bf.email"].with_user(self.writer)
        self._placement("brouillon")
        self.assertIn("Société A", Boite.inbox_compose()["context"]["default_body"])
        self._placement("envoi")
        self.assertNotIn("Société A", Boite.inbox_compose()["context"]["default_body"])

    # ------------------------------------------------------------------
    # 5. Résolution et semis
    # ------------------------------------------------------------------
    def test_the_default_identity_is_the_flagged_one(self):
        Identity = self.env["bf.email.identity"]
        self.assertEqual(Identity._default_for(self.writer), self.primary)

    def test_only_one_default_survives(self):
        self.second.sudo().is_default = True
        self.assertFalse(self.primary.sudo().is_default)

    def test_a_reply_defaults_to_the_mailbox_that_received(self):
        """Répondre depuis la boîte qui a reçu est le seul défaut qui ne surprend personne."""
        account = self.env["bf.email.account"].sudo().create({
            "name": "Boîte B",
            "user_id": self.writer.id,
            "host": "imap.societe-b.test",
            "login": "jane@societe-b.test",
            "password": "x",
        })
        row = self.env["bf.email"].sudo().create({
            "subject": "Entrant",
            "direction": "in",
            "user_id": self.writer.id,
            "account_id": account.id,
            "email_from": "client@ailleurs.test",
            "date": fields.Datetime.now(),
        })
        resolved = row.with_user(self.writer)._compose_identity()
        self.assertEqual(resolved.email_normalized, "jane@societe-b.test")

    def test_seeding_is_idempotent(self):
        Identity = self.env["bf.email.identity"]
        before = Identity.sudo().search_count([("user_id", "=", self.writer.id)])
        Identity._sync_from_accounts(self.writer)
        Identity._sync_from_accounts(self.writer)
        after = Identity.sudo().search_count([("user_id", "=", self.writer.id)])
        self.assertEqual(before, after)

    def test_seeding_marks_proven_addresses_verified(self):
        """L'adresse de la fiche et un login IMAP sont des possessions démontrées."""
        Identity = self.env["bf.email.identity"]
        fresh = self.env["res.users"].with_context(
            no_reset_password=True).create({
                "name": "Neuve",
                "login": "sendas.fresh@test.invalid",
                "email": "neuve@societe-a.test",
                "groups_id": [(6, 0, [self.env.ref("base.group_user").id])],
            })
        Identity._sync_from_accounts(fresh)
        seeded = Identity.sudo().search([("user_id", "=", fresh.id)])
        self.assertTrue(seeded)
        self.assertTrue(all(seeded.mapped("verified")))

    def test_a_new_imap_account_gets_its_identity(self):
        self.env["bf.email.account"].sudo().create({
            "name": "Boîte C",
            "user_id": self.writer.id,
            "host": "imap.societe-c.test",
            "login": "jane@societe-c2.test",
            "password": "x",
        })
        found = self.env["bf.email.identity"].sudo().search([
            ("user_id", "=", self.writer.id),
            ("email_normalized", "=", "jane@societe-c2.test"),
        ])
        self.assertTrue(found)

    def test_the_same_address_cannot_be_declared_twice(self):
        from psycopg2 import IntegrityError
        with self.assertRaises(IntegrityError), self.cr.savepoint():
            self.env["bf.email.identity"].sudo().create({
                "user_id": self.writer.id,
                "name": "Doublon",
                "email": "jane@societe-b.test",
            })

    def test_an_invalid_address_is_refused(self):
        with self.assertRaises(ValidationError):
            self.env["bf.email.identity"].sudo().create({
                "user_id": self.writer.id,
                "name": "Cassée",
                "email": "pas-une-adresse",
            })

    # ------------------------------------------------------------------
    # 6. Délivrabilité : dire ce qui manque plutôt que le laisser découvrir
    # ------------------------------------------------------------------
    def test_an_address_no_server_covers_is_flagged(self):
        self.env["ir.mail_server"].sudo().search([]).unlink()
        self.env["ir.mail_server"].sudo().create({
            "name": "Société A seulement",
            "smtp_host": "smtp.societe-a.test",
            "from_filter": "societe-a.test",
        })
        orphan = self.env["bf.email.identity"].sudo().create({
            "user_id": self.writer.id,
            "name": "Jane",
            "email": "jane@societe-z.test",
            "verified": True,
        })
        self.assertTrue(orphan.delivery_warning)

    def test_a_covered_address_is_not_flagged(self):
        self.env["ir.mail_server"].sudo().search([]).unlink()
        self.env["ir.mail_server"].sudo().create({
            "name": "Société B",
            "smtp_host": "smtp.societe-b.test",
            "from_filter": "societe-b.test",
        })
        self.second.sudo().invalidate_recordset(["delivery_warning"])
        self.assertFalse(self.second.sudo().delivery_warning)
