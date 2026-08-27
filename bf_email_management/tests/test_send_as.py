"""Écrire sous une autre de ses adresses, sans devenir quelqu'un d'autre.

Ce que ces tests tiennent, dans l'ordre où ça compte :

1. le « De » sort réellement sous l'identité choisie, et l'auteur reste la
   personne qui écrit ;
2. sans identité, rien ne change — la surcharge ne doit pas déplacer le « De »
   de tous les composeurs de l'instance ;
3. on ne peut pas porter l'identité d'autrui, ni une identité que personne
   n'a vérifiée ;
4. la signature suit l'identité, et changer d'identité ne mange pas le texte
   déjà tapé.

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
    # 4. La signature suit, sans manger le texte
    # ------------------------------------------------------------------
    def test_the_signature_follows_the_identity(self):
        self.assertIn("Société B", self.second._signature_for())

    def test_an_identity_without_signature_falls_back_to_the_user(self):
        self.assertIn("Société A", self.primary._signature_for())

    def test_switching_identity_swaps_the_signature(self):
        composer = self.env["mail.compose.message"].with_user(self.writer).create({
            "model": "res.partner",
            "res_ids": repr([self.carrier.id]),
            "composition_mode": "comment",
            "body": "<p>Bonjour</p>" + self.primary._signature_for(),
            "bf_signature_snapshot": self.primary._signature_for(),
        })
        composer.bf_identity_id = self.second
        composer._onchange_bf_identity_id()
        self.assertIn("Société B", composer.body)
        self.assertNotIn("Société A", composer.body)
        self.assertIn("Bonjour", composer.body)

    def test_a_touched_signature_is_left_alone(self):
        """Perdre une phrase tapée pour ajuster une signature serait un mauvais marché."""
        composer = self.env["mail.compose.message"].with_user(self.writer).create({
            "model": "res.partner",
            "res_ids": repr([self.carrier.id]),
            "composition_mode": "comment",
            "body": "<p>Bonjour</p><p>-- <br/>signature retouchée à la main</p>",
            "bf_signature_snapshot": self.primary._signature_for(),
        })
        composer.bf_identity_id = self.second
        composer._onchange_bf_identity_id()
        self.assertIn("retouchée à la main", composer.body)

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
