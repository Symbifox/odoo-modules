"""« Répondre à tous » depuis le chatter :.

Ce que ces tests tiennent, dans l'ordre où ça coûte :

1. **la passerelle ne se retrouve jamais en copie.** C'est le vrai risque de
   la fonction : le ``To:`` de tout courriel entrant est le catchall de
   l'instance. Un « Répondre à tous » qui le garde renvoie le message à Odoo,
   qui le reposte dans le chatter d'où il vient ;
2. **les destinataires sortent du bon endroit.** Le chatter ne garde pas les
   en-têtes ``To:``/``Cc:`` d'un entrant : il faut le miroir ``bf.email``. Un
   message sortant, lui, porte ses propres listes ;
3. **on ne s'écrit pas à soi-même**, et un nom affiché à virgule ne compte
   pas pour deux personnes ;
4. **le composeur reçoit vraiment ces listes**, jusque dans son contexte.
"""

from odoo import Command, fields
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class ChatterReplyAllCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Users = cls.env["res.users"].with_context(no_reset_password=True)
        cls.replier = Users.create({
            "name": "Répondeuse",
            "login": "reply.all@test.invalid",
            "email": "moi@exemple.test",
            "groups_id": [Command.set([
                cls.env.ref("base.group_user").id,
                cls.env.ref("base.group_partner_manager").id,
            ])],
        })

        # Le domaine d'alias porte le catchall et le bounce : c'est là qu'Odoo
        # 18 les range, et c'est ce que l'ancienne exclusion ne lisait pas.
        cls.alias_domain = cls.env["mail.alias.domain"].create({
            "name": "exemple.test",
            "catchall_alias": "bonjour",
            "bounce_alias": "retour",
            "default_from": "notifications",
        })
        cls.env["mail.alias"].create({
            "alias_name": "depenses",
            "alias_domain_id": cls.alias_domain.id,
            "alias_model_id": cls.env["ir.model"]._get("res.partner").id,
        })

        cls.record = cls.env["res.partner"].create({"name": "Fiche porteuse"})
        cls.sender = cls.env["res.partner"].create({
            "name": "Cliente", "email": "cliente@dehors.test",
        })
        cls.colleague = cls.env["res.partner"].create({
            "name": "Collègue", "email": "collegue@dehors.test",
        })

    # ------------------------------------------------------------------
    # Outils
    # ------------------------------------------------------------------
    def _message(self, **vals):
        base = {
            "model": "res.partner",
            "res_id": self.record.id,
            "message_type": "email",
            "subtype_id": self.env.ref("mail.mt_comment").id,
            "subject": "Sujet",
            "body": "<p>corps</p>",
        }
        base.update(vals)
        return self.env["mail.message"].create(base)

    def _emails(self, ids):
        return sorted(self.env["res.partner"].browse(ids).mapped("email"))

    # ------------------------------------------------------------------
    # 1. La passerelle
    # ------------------------------------------------------------------
    def test_routing_addresses_viennent_du_domaine_dalias(self):
        addrs = self.env["bf.email"]._bf_routing_addresses()
        self.assertIn("bonjour@exemple.test", addrs)
        self.assertIn("retour@exemple.test", addrs)
        self.assertIn("notifications@exemple.test", addrs)
        self.assertIn("depenses@exemple.test", addrs)

    def test_catchall_jamais_en_copie(self):
        """Le cas réel : entrant adressé au catchall, un tiers en Cc."""
        msg = self._message(
            email_from="Cliente <cliente@dehors.test>",
            message_id="<catchall@test.invalid>",
        )
        self.env["bf.email"].create({
            "date": fields.Datetime.now(),
            "message_id_header": "<catchall@test.invalid>",
            "direction": "in",
            "email_from": "Cliente <cliente@dehors.test>",
            "email_to": "bonjour@exemple.test",
            "email_cc": "Collègue <collegue@dehors.test>",
            "subject": "Sujet",
            "user_id": self.replier.id,
        })
        to_ids, cc_ids = msg.with_user(self.replier)._bf_reply_all_recipients()
        self.assertEqual(self._emails(to_ids), ["cliente@dehors.test"])
        self.assertEqual(self._emails(cc_ids), ["collegue@dehors.test"])
        self.assertNotIn(
            "bonjour@exemple.test",
            self._emails(to_ids) + self._emails(cc_ids),
        )

    def test_alias_de_modele_jamais_en_copie(self):
        msg = self._message(
            email_from="Cliente <cliente@dehors.test>",
            message_id="<alias@test.invalid>",
        )
        self.env["bf.email"].create({
            "date": fields.Datetime.now(),
            "message_id_header": "<alias@test.invalid>",
            "direction": "in",
            "email_from": "cliente@dehors.test",
            "email_to": "depenses@exemple.test, collegue@dehors.test",
            "subject": "Sujet",
            "user_id": self.replier.id,
        })
        _to_ids, cc_ids = msg.with_user(self.replier)._bf_reply_all_recipients()
        self.assertEqual(self._emails(cc_ids), ["collegue@dehors.test"])

    # ------------------------------------------------------------------
    # 2. D'où sortent les destinataires
    # ------------------------------------------------------------------
    def test_entrant_sans_miroir_ne_perd_pas_lauteur(self):
        """Sans miroir ni en-têtes, il reste l'auteur, et c'est déjà ça."""
        msg = self._message(
            email_from="Cliente <cliente@dehors.test>",
            author_id=self.sender.id,
            message_id="<nu@test.invalid>",
        )
        to_ids, cc_ids = msg.with_user(self.replier)._bf_reply_all_recipients()
        self.assertEqual(self._emails(to_ids), ["cliente@dehors.test"])
        self.assertEqual(cc_ids, [])

    def test_sortant_lit_ses_propres_listes(self):
        """Ce que NOUS avons envoyé : Odoo a composé la liste, elle est juste."""
        msg = self._message(
            message_type="comment",
            email_from="moi@exemple.test",
            author_id=self.replier.partner_id.id,
            message_id="<sortant@test.invalid>",
            partner_ids=[Command.set([self.sender.id])],
            recipient_cc_ids=[Command.set([self.colleague.id])],
        )
        to_ids, cc_ids = msg.with_user(self.replier)._bf_reply_all_recipients()
        self.assertEqual(self._emails(to_ids), ["cliente@dehors.test"])
        self.assertEqual(self._emails(cc_ids), ["collegue@dehors.test"])

    def test_miroir_et_partenaires_sadditionnent(self):
        """Les deux sources comptent : élire la mieux fournie perd des gens.

        Le miroir connaît un Cc que le chatter n'a jamais vu ; le message
        porte un partenaire que le miroir ignore, parce que
        ``_prepare_email_vals`` ne lit pas ``recipient_cc_ids``.
        """
        inconnu = "jamais.vu@dehors.test"
        msg = self._message(
            email_from="cliente@dehors.test",
            message_id="<miroir@test.invalid>",
            recipient_cc_ids=[Command.set([self.colleague.id])],
        )
        self.env["bf.email"].create({
            "date": fields.Datetime.now(),
            "message_id_header": "<miroir@test.invalid>",
            "direction": "in",
            "email_from": "cliente@dehors.test",
            "email_to": "bonjour@exemple.test",
            "email_cc": inconnu,
            "subject": "Sujet",
            "user_id": self.replier.id,
        })
        _to_ids, cc_ids = msg.with_user(self.replier)._bf_reply_all_recipients()
        self.assertEqual(
            self._emails(cc_ids), sorted(["collegue@dehors.test", inconnu]),
        )

    def test_sortant_sans_destinataire_ne_sadresse_pas_a_soi(self):
        """🔴 Le repli sur l'auteur ne vaut que pour un entrant."""
        msg = self._message(
            message_type="comment",
            email_from="moi@exemple.test",
            author_id=self.replier.partner_id.id,
            message_id="<vide@test.invalid>",
        )
        to_ids, cc_ids = msg.with_user(self.replier)._bf_reply_all_recipients()
        self.assertEqual(to_ids, [])
        self.assertEqual(cc_ids, [])

    def test_sans_personne_a_qui_repondre_on_le_dit(self):
        """Pas de composeur vide : un composeur vide part « à personne »."""
        msg = self._message(
            message_type="comment",
            email_from="moi@exemple.test",
            author_id=self.replier.partner_id.id,
            message_id="<rien@test.invalid>",
        )
        action = msg.with_user(self.replier).action_bf_reply_all()
        self.assertEqual(action["tag"], "display_notification")
        self.assertEqual(action["params"]["type"], "warning")

    # ------------------------------------------------------------------
    # 3. Soi-même, et les virgules
    # ------------------------------------------------------------------
    def test_on_ne_secrit_pas_a_soi_meme(self):
        msg = self._message(
            email_from="cliente@dehors.test",
            message_id="<moi@test.invalid>",
        )
        self.env["bf.email"].create({
            "date": fields.Datetime.now(),
            "message_id_header": "<moi@test.invalid>",
            "direction": "in",
            "email_from": "cliente@dehors.test",
            "email_to": "moi@exemple.test, collegue@dehors.test",
            "subject": "Sujet",
            "user_id": self.replier.id,
        })
        _to_ids, cc_ids = msg.with_user(self.replier)._bf_reply_all_recipients()
        self.assertEqual(self._emails(cc_ids), ["collegue@dehors.test"])

    def test_nom_affiche_a_virgule_reste_une_personne(self):
        msg = self._message(
            email_from="cliente@dehors.test",
            message_id="<virgule@test.invalid>",
        )
        self.env["bf.email"].create({
            "date": fields.Datetime.now(),
            "message_id_header": "<virgule@test.invalid>",
            "direction": "in",
            "email_from": "cliente@dehors.test",
            "email_to": '"Béland, François" <fbeland@dehors.test>',
            "subject": "Sujet",
            "user_id": self.replier.id,
        })
        _to_ids, cc_ids = msg.with_user(self.replier)._bf_reply_all_recipients()
        self.assertEqual(self._emails(cc_ids), ["fbeland@dehors.test"])
        partner = self.env["res.partner"].browse(cc_ids)
        self.assertEqual(partner.name, "Béland, François")

    def test_un_destinataire_ne_figure_pas_deux_fois(self):
        msg = self._message(
            email_from="cliente@dehors.test",
            message_id="<double@test.invalid>",
        )
        self.env["bf.email"].create({
            "date": fields.Datetime.now(),
            "message_id_header": "<double@test.invalid>",
            "direction": "in",
            "email_from": "cliente@dehors.test",
            "email_to": "collegue@dehors.test, Cliente <cliente@dehors.test>",
            "email_cc": "COLLEGUE@dehors.test",
            "subject": "Sujet",
            "user_id": self.replier.id,
        })
        to_ids, cc_ids = msg.with_user(self.replier)._bf_reply_all_recipients()
        self.assertEqual(self._emails(to_ids), ["cliente@dehors.test"])
        self.assertEqual(self._emails(cc_ids), ["collegue@dehors.test"])
        self.assertEqual(len(cc_ids), 1)

    def test_sortant_ne_met_pas_le_meme_en_a_et_en_copie(self):
        """Le dédoublonnage entre « À » et « Cc » sert surtout au sortant.

        Sur un entrant, l'expéditeur est déjà écarté du Cc par construction ;
        c'est ici, quand la même personne figure dans les deux listes qu'Odoo
        a composées, que ``skip_ids`` gagne sa place.
        """
        msg = self._message(
            message_type="comment",
            email_from="moi@exemple.test",
            author_id=self.replier.partner_id.id,
            message_id="<sortantdouble@test.invalid>",
            partner_ids=[Command.set([self.sender.id])],
            recipient_cc_ids=[Command.set([self.sender.id, self.colleague.id])],
        )
        to_ids, cc_ids = msg.with_user(self.replier)._bf_reply_all_recipients()
        self.assertEqual(self._emails(to_ids), ["cliente@dehors.test"])
        self.assertEqual(self._emails(cc_ids), ["collegue@dehors.test"])

    def test_les_alias_du_compte_imap_sont_ecartes(self):
        """Une adresse à soi qui n'est pas celle de sa fiche compte quand même.

        ``_get_self_addresses`` lit les logins et les alias des comptes
        ``bf.email.account`` de l'usager. Sans cette lecture, une réponse
        remettrait en copie une boîte que la personne relève elle-même.
        """
        self.env["bf.email.account"].create({
            "name": "Boîte partagée",
            "host": "imap.exemple.test",
            "login": "equipe@exemple.test",
            "password": "sans-importance",
            "email_aliases": "ventes@exemple.test",
            "user_id": self.replier.id,
        })
        msg = self._message(
            email_from="cliente@dehors.test",
            message_id="<alias-compte@test.invalid>",
        )
        self.env["bf.email"].create({
            "date": fields.Datetime.now(),
            "message_id_header": "<alias-compte@test.invalid>",
            "direction": "in",
            "email_from": "cliente@dehors.test",
            "email_to": "equipe@exemple.test, collegue@dehors.test",
            "email_cc": "ventes@exemple.test",
            "subject": "Sujet",
            "user_id": self.replier.id,
        })
        _to_ids, cc_ids = msg.with_user(self.replier)._bf_reply_all_recipients()
        self.assertEqual(self._emails(cc_ids), ["collegue@dehors.test"])

    # ------------------------------------------------------------------
    # 4. Le composeur reçoit bien tout ça
    # ------------------------------------------------------------------
    def test_action_remplit_le_contexte_du_composeur(self):
        msg = self._message(
            email_from="Cliente <cliente@dehors.test>",
            subject="Re: Sujet",
            message_id="<action@test.invalid>",
        )
        self.env["bf.email"].create({
            "date": fields.Datetime.now(),
            "message_id_header": "<action@test.invalid>",
            "direction": "in",
            "email_from": "Cliente <cliente@dehors.test>",
            "email_to": "bonjour@exemple.test",
            "email_cc": "Collègue <collegue@dehors.test>",
            "subject": "Re: Sujet",
            "user_id": self.replier.id,
        })
        action = msg.with_user(self.replier).action_bf_reply_all()
        ctx = action["context"]
        self.assertEqual(ctx["default_model"], "res.partner")
        self.assertEqual(ctx["default_res_ids"], [self.record.id])
        self.assertEqual(self._emails(ctx["default_partner_ids"][0][2]),
                         ["cliente@dehors.test"])
        self.assertEqual(self._emails(ctx["default_partner_cc_ids"][0][2]),
                         ["collegue@dehors.test"])
        # L'objet ne s'empile pas : « Re: Re: » est une régression connue de
        # mail_quoted_reply, déjà rectifiée par reply_message() ici.
        self.assertEqual(ctx["default_subject"], "Re: Sujet")
        self.assertTrue(ctx.get("is_quoted_reply"))
        self.assertIn("cliente@dehors.test", ctx["quote_body"])
