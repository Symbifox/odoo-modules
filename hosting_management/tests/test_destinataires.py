# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from odoo import Command
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestDestinataires(TransactionCase):
    """Les destinataires hébergement d'un client."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Partner = cls.env["res.partner"]
        cls.client_a = Partner.create({"name": "Client Alpha Dest", "is_company": True,
                                       "email": "accueil@alpha-dest.example"})
        cls.ana = Partner.create({"name": "Ana Alpha", "parent_id": cls.client_a.id,
                                  "email": "ana@alpha-dest.example"})
        cls.sans_courriel = Partner.create({"name": "Sans Courriel", "parent_id": cls.client_a.id})
        cls.client_b = Partner.create({"name": "Client Bêta Dest", "is_company": True})
        cls.bob = Partner.create({"name": "Bob Bêta", "parent_id": cls.client_b.id,
                                  "email": "bob@beta-dest.example"})
        cls.gestion = new_test_user(cls.env, login="essai_dest_hebergement",
                                    groups="base.group_user,base.group_partner_manager,hosting_management.group_hosting_user")
        cls.contacts = new_test_user(cls.env, login="essai_dest_contacts",
                                     groups="base.group_user,base.group_partner_manager,base.group_erp_manager")
        # Sur une base d'essai, créer un contact peut exiger aussi `base.group_erp_manager`.
        cls.gestion_contacts = new_test_user(
            cls.env, login="essai_dest_hebergement_contacts",
            groups="base.group_user,base.group_partner_manager,base.group_erp_manager,"
                   "hosting_management.group_hosting_user")

    def test_la_societe_ou_ses_contacts(self):
        self.client_a.hosting_recipient_ids = self.client_a | self.ana
        self.assertEqual(self.client_a.hosting_recipient_ids, self.client_a | self.ana)
        with self.assertRaises(ValidationError):
            self.client_a.hosting_recipient_ids = self.bob
        with self.assertRaises(ValidationError):
            self.ana.hosting_recipient_ids = self.ana

    def test_la_liste_tenue_passe_d_abord(self):
        self.client_a.hosting_recipient_ids = self.ana | self.sans_courriel
        self.assertEqual(self.client_a._hosting_destinataires(), self.ana)
        # Depuis un contact, on remonte au client.
        self.assertEqual(self.ana._hosting_destinataires(), self.ana)

    def test_un_contact_parti_ailleurs_ne_recoit_plus_rien(self):
        self.client_a.hosting_recipient_ids = self.ana
        self.ana.parent_id = self.client_b
        self.assertEqual(self.client_a._hosting_destinataires(), self.client_a)
        self.ana.parent_id = self.client_a
        self.ana.active = False
        self.assertEqual(self.client_a._hosting_destinataires(), self.client_a)

    def test_l_ancienne_regle_sans_liste(self):
        self.assertEqual(self.client_a._hosting_destinataires(), self.client_a)
        self.assertEqual(self.client_b._hosting_destinataires(), self.bob)
        self.bob.email = False
        self.assertFalse(self.client_b._hosting_destinataires())

    def test_reserve_au_groupe_hebergement(self):
        Partner = self.env["res.partner"]
        with self.assertRaises(AccessError):
            self.client_a.with_user(self.contacts).write(
                {"hosting_recipient_ids": [Command.set(self.ana.ids)]})
        # 🔴 Odoo ne contrôle pas le groupe d'un champ à la création, et la contrainte ne
        # suffit pas : une société neuve rattache le contact d'un autre client (`child_ids`)
        # puis le nomme, ce qui est cohérent pour elle. La garde de `create` refuse.
        with self.assertRaises(AccessError):
            Partner.with_user(self.contacts).create({
                "name": "Société Piège", "is_company": True,
                "child_ids": [Command.link(self.bob.id)],
                "hosting_recipient_ids": [Command.set(self.bob.ids)],
            })
        self.assertEqual(self.bob.parent_id, self.client_b)
        # Sans liste, la même création passe : la garde ne vise que la liste.
        neuve = Partner.with_user(self.contacts).create({"name": "Société Neuve", "is_company": True})
        self.assertFalse(neuve.sudo().hosting_recipient_ids)
        self.client_a.with_user(self.gestion).hosting_recipient_ids = self.ana
        self.assertEqual(self.client_a.hosting_recipient_ids, self.ana)

    def test_la_garde_de_creation_avec_le_groupe(self):
        # Avec le groupe, la même création passe.
        Partner = self.env["res.partner"]
        cree = Partner.with_user(self.gestion_contacts).create({
            "name": "Société Rattachante", "is_company": True,
            "child_ids": [Command.link(self.bob.id)],
            "hosting_recipient_ids": [Command.set(self.bob.ids)],
        })
        self.assertEqual(cree.sudo().hosting_recipient_ids, self.bob)

    def test_dupliquer_sans_le_groupe(self):
        # 🔴 Copiable, le champ était lu par `copy_data` : AccessError pour un interne
        # sans le groupe, sur un contact comme sur un usager (`_inherits`).
        self.client_a.hosting_recipient_ids = self.client_a | self.ana
        copie = self.ana.with_user(self.contacts).copy()
        self.assertFalse(copie.sudo().hosting_recipient_ids)
        self.client_a.with_user(self.contacts).copy()
        # L'usager : `copy_data` (où le champ hérité par `_inherits` était lu). Un `copy()`
        # complet bute ici sur une autre ACL (`action_id`, ir.actions.client), hors sujet.
        self.gestion.with_user(self.contacts).copy_data()

    def test_dupliquer_une_societe_qui_a_une_liste(self):
        # Copiée, la liste nommait les contacts de l'original sur la copie : ValidationError.
        self.client_a.hosting_recipient_ids = self.client_a | self.ana
        copie = self.client_a.with_user(self.gestion_contacts).copy()
        self.assertFalse(copie.sudo().hosting_recipient_ids)
        self.assertEqual(self.client_a.hosting_recipient_ids, self.client_a | self.ana)

    def test_le_repli_ignore_une_societe_enfant(self):
        Partner = self.env["res.partner"]
        client_g = Partner.create({"name": "Client Gamma Dest", "is_company": True})
        # Nommée pour passer AVANT le contact dans l'ordre de `child_ids` (complete_name) :
        # sans le filtre, le repli la rendrait.
        filiale = Partner.create({"name": "Atelier Gamma Filiale", "is_company": True,
                                  "parent_id": client_g.id, "email": "filiale@gamma-dest.example"})
        cora = Partner.create({"name": "Cora Gamma", "parent_id": client_g.id,
                               "email": "cora@gamma-dest.example"})
        self.assertEqual(client_g.child_ids.filtered("email")[:1], filiale)
        self.assertEqual(client_g._hosting_destinataires(), cora)
        cora.email = False
        self.assertFalse(client_g._hosting_destinataires())

    def test_rattacher_un_particulier_destinataire_de_lui_meme(self):
        Partner = self.env["res.partner"]
        particulier = Partner.create({"name": "Pierre Particulier",
                                      "email": "pierre@particulier.example"})
        particulier.hosting_recipient_ids = particulier
        self.assertEqual(particulier._hosting_destinataires(), particulier)
        # Un usager contacts sans le groupe le rattache à une société : la contrainte ne se
        # déclenche que sur la liste, il ne bute plus sur une liste qu'il ne voit pas.
        particulier.with_user(self.contacts).parent_id = self.client_a
        # La ligne « lui-même » reste en base, sans effet : on remonte au client, et
        # `client_de(particulier)` n'est plus lui-même. Le client n'a pas de liste et a un
        # courriel : c'est la société qui est rendue, pas l'ancien particulier.
        self.assertEqual(particulier.hosting_recipient_ids, particulier)
        self.assertEqual(particulier._hosting_destinataires(), self.client_a)
        self.assertNotIn(particulier, particulier._hosting_destinataires())

    def test_la_fiche_contact_s_ouvre_sans_le_groupe(self):
        # Le nœud disparaît pour qui n'a pas le groupe ; la lecture champ par champ de toute
        # la fiche est faite par `test_fiche_sans_groupe` (préfixe `hosting_`).
        vues = self.env["res.partner"].with_user(self.contacts).get_views([(False, "form")])
        self.assertNotIn('name="hosting_recipient_ids"', vues["views"]["form"]["arch"])
        with self.assertRaises(AccessError):
            self.client_a.with_user(self.contacts).read(["hosting_recipient_ids"])

    def test_l_avis_porte_le_lien_seulement_s_il_est_donne(self):
        gabarit = self.env.ref("hosting_management.mail_template_client_maintenance_notice")
        sans = gabarit._render_field("body_html", self.client_a.ids)[self.client_a.id]
        self.assertNotIn("status page", sans)
        avec = gabarit.with_context(status_url="https://odoo.example/etat/1/jeton-essai")._render_field(
            "body_html", self.client_a.ids)[self.client_a.id]
        self.assertIn('href="https://odoo.example/etat/1/jeton-essai"', avec)

    def test_l_avis_en_francais_porte_le_lien(self):
        from odoo.addons.hosting_management.models.hosting_maintenance_window import (
            recharger_traduction_avis)
        gabarit = self.env.ref("hosting_management.mail_template_client_maintenance_notice")
        # La traduction d'avant le lot, déjà en base : une mise à jour ne la remplace pas.
        gabarit.with_context(lang="fr_CA").body_html = "<p>Avis d'avant, sans lien</p>"
        recharger_traduction_avis(self.env)
        self.client_a.lang = "fr_CA"
        gabarit = self.env.ref("hosting_management.mail_template_client_maintenance_notice")
        corps = gabarit.with_context(lang="fr_CA", status_url="https://odoo.example/etat/1/jeton-essai")._render_field(
            "body_html", self.client_a.ids, compute_lang=True)[self.client_a.id]
        self.assertIn("votre page d'état", corps)
        self.assertNotIn("your status page", corps)
