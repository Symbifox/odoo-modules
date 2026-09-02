"""Cloisonnement des boîtes : qui voit quoi, et par quelle porte.

Le contrat que ce module doit tenir :

* un courriel n'est visible que par le **propriétaire** de la boîte
  (`bf.email.user_id`) ;
* sauf pour qui porte le groupe « Gestion des courriels / Administrateur —
  tous les courriels », coché dans la fiche de l'usager, **en lecture seule** ;
* un compte IMAP (qui porte le mot de passe) n'est visible QUE de son
  propriétaire — l'administrateur courriel n'y a pas accès non plus ;
* et la boîte de réception de chacun reste la sienne, y compris pour un
  administrateur : voir tout ne veut pas dire compter tout dans SON écran.

Les deux dernières classes éprouvent la surface de l'arbre des dossiers
IMAP, parce qu'une méthode publique sur un modèle est
appelable par `call_kw` depuis la console du navigateur de n'importe quel
usager interne.
"""
import json

from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .common import MobileApiCase


class IsolationCase(MobileApiCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.email_admin = cls.env["res.users"].with_context(
            no_reset_password=True).create({
                "name": "Admin Courriel",
                "login": "admin.courriel@test.invalid",
                "email": "admin.courriel@test.invalid",
                "groups_id": [(6, 0, [
                    cls.env.ref("base.group_user").id,
                    cls.env.ref(
                        "bf_email_management.group_email_admin").id,
                ])],
            })


@tagged("post_install", "-at_install")
class TestOwnerOnly(IsolationCase):
    """Un usager ordinaire ne voit que sa propre boîte."""

    def test_a_colleague_row_is_not_searchable(self):
        found = self.env["bf.email"].with_user(self.stranger).search([])
        self.assertNotIn(self.inbound.id, found.ids)
        self.assertIn(self.foreign.id, found.ids)

    def test_a_colleague_row_cannot_be_read_by_id(self):
        # Connaître l'id ne suffit pas : c'est la règle qui décide, pas la vue.
        with self.assertRaises(AccessError):
            self.env["bf.email"].with_user(self.stranger).browse(
                self.inbound.id).read(["subject", "body_html"])

    def test_a_colleague_row_cannot_be_written(self):
        with self.assertRaises(AccessError):
            self.env["bf.email"].with_user(self.stranger).browse(
                self.inbound.id).write({"is_handled": True})

    def test_a_colleague_row_cannot_be_handled_through_the_action(self):
        # La porte des actions doit être aussi close que celle des champs.
        with self.assertRaises(AccessError):
            self.env["bf.email"].with_user(self.stranger).browse(
                self.inbound.id).action_archive()

    def test_the_inbox_of_a_colleague_is_empty_of_my_rows(self):
        page = self.env["bf.email"].with_user(self.stranger).inbox_get_messages(
            "all", 0, 200)
        self.assertNotIn(self.inbound.id, [m["id"] for m in page["messages"]])

    def test_a_colleague_body_cannot_be_pulled_by_the_inbox_rpc(self):
        # `inbox_get_body` pose un `check_access("read")` explicite : la
        # méthode est appelable, la ligne ne l'est pas.
        with self.assertRaises(AccessError):
            self.env["bf.email"].with_user(self.stranger).inbox_get_body(
                self.inbound.id)


@tagged("post_install", "-at_install")
class TestEmailAdmin(IsolationCase):
    """La case « Administrateur — tous les courriels » : lecture, et rien d'autre."""

    def test_the_box_ticked_opens_every_mailbox_in_reading(self):
        found = self.env["bf.email"].with_user(self.email_admin).search([])
        self.assertIn(self.inbound.id, found.ids)
        self.assertIn(self.foreign.id, found.ids)

    def test_the_box_unticked_closes_them_again(self):
        # Le même usager, la case retirée : il ne reste que la sienne.
        self.email_admin.groups_id -= self.env.ref(
            "bf_email_management.group_email_admin")
        found = self.env["bf.email"].with_user(self.email_admin).search([])
        self.assertNotIn(self.inbound.id, found.ids)
        self.assertNotIn(self.foreign.id, found.ids)

    def test_reading_everything_is_not_writing_everything(self):
        with self.assertRaises(AccessError):
            self.env["bf.email"].with_user(self.email_admin).browse(
                self.inbound.id).write({"subject": "réécrit"})

    def test_reading_everything_is_not_deleting_everything(self):
        with self.assertRaises(AccessError):
            self.env["bf.email"].with_user(self.email_admin).browse(
                self.inbound.id).unlink()

    def test_the_imap_account_stays_shut_even_to_the_email_admin(self):
        # C'est là que vit le mot de passe IMAP : aucune règle « admin voit
        # tout » ne porte sur bf.email.account, et ça doit le rester.
        with self.assertRaises(AccessError):
            self.env["bf.email.account"].with_user(self.email_admin).browse(
                self.account.id).read(["login", "password"])

    def test_the_admin_inbox_counts_only_the_admin_own_mail(self):
        # Voir tout ne veut pas dire compter tout : le badge et la colonne de
        # gauche sont bornés à `user_id = uid`, pas à ce que la règle laisse
        # lire. Sans ça un administrateur voit « 99+ » en étant à inbox zéro.
        folders = {f["key"]: f for f in
                   self.env["bf.email"].with_user(
                       self.email_admin).inbox_get_folders()}
        self.assertEqual(folders["all"]["count"], 0)
        page = self.env["bf.email"].with_user(self.email_admin).\
            inbox_get_messages("all", 0, 200)
        self.assertEqual(page["total"], 0)


@tagged("post_install", "-at_install")
class TestImapFolderTreeIsolation(IsolationCase):
    """L'arbre des dossiers IMAP (#24976) ne doit rien laisser filtrer."""

    def setUp(self):
        super().setUp()
        self.cache = json.dumps([
            {"name": "INBOX", "delimiter": "/",
             "has_children": False, "noselect": False},
        ])
        self.account.write({"folder_cache": self.cache,
                            "folder_cache_date": "2999-01-01 00:00:00"})

    def test_a_colleague_sees_none_of_my_folders(self):
        keys = [f["key"] for f in
                self.env["bf.email"].with_user(self.stranger).inbox_get_folders()]
        self.assertNotIn("imapf:%s:INBOX" % self.account.id, keys)

    def test_the_email_admin_sees_none_of_my_folders_either(self):
        keys = [f["key"] for f in
                self.env["bf.email"].with_user(
                    self.email_admin).inbox_get_folders()]
        self.assertNotIn("imapf:%s:INBOX" % self.account.id, keys)

    def test_forging_a_folder_key_does_not_open_a_colleague_mailbox(self):
        # La clé arrive du navigateur : la propriété du compte est vérifiée,
        # jamais déduite de la clé.
        for who in (self.stranger, self.email_admin):
            with self.assertRaises(UserError):
                self.env["bf.email"].with_user(who)._inbox_folder_domain(
                    "imapf:%s:INBOX" % self.account.id)
            with self.assertRaises(UserError):
                self.env["bf.email"].with_user(who)._inbox_folder_domain(
                    "imapacct:%s" % self.account.id)

    def test_the_folder_counts_never_borrow_a_colleague_row(self):
        # Une ligne du voisin rattachée à MON compte ne doit pas gonfler mon
        # compteur — le regroupement porte user_id ET account_id.
        self.foreign.sudo().write({"account_id": self.account.id,
                                   "imap_folder": "INBOX"})
        folders = {f["key"]: f for f in self.as_owner().inbox_get_folders()}
        key = "imapf:%s:INBOX" % self.account.id
        self.assertEqual(folders[key]["count"], 3)


@tagged("post_install", "-at_install")
class TestAccountMethodsOverRpc(IsolationCase):
    """Les méthodes publiques de bf.email.account sont des portes ouvertes.

    Une méthode sans tiret bas est appelable par `call_kw` depuis la console
    du navigateur de n'importe quel usager interne, sur n'importe quel id.
    Ce qui la protège n'est pas la vue, c'est ce qu'elle touche.
    """

    # Les deux méthodes de découverte sont privées : hors d'atteinte de
    # `call_kw`, qui refuse tout nom commençant par un tiret bas.
    def test_the_discovery_methods_are_not_callable_over_rpc(self):
        for name in ("_get_imap_folders", "_store_imap_folders"):
            self.assertTrue(
                name.startswith("_"),
                "%s doit rester privée : publique, elle est une porte RPC "
                "sur l'id de n'importe quel compte" % name,
            )
            self.assertTrue(hasattr(type(self.account), name))
        self.assertFalse(hasattr(type(self.account), "get_imap_folders"))
        self.assertFalse(hasattr(type(self.account), "store_imap_folders"))

    def test_reading_a_colleague_folder_tree_is_refused(self):
        with self.assertRaises(AccessError):
            self.env["bf.email.account"].with_user(self.stranger).browse(
                self.account.id)._get_imap_folders()

    def test_refreshing_a_colleague_folder_tree_is_refused(self):
        # Celle-ci reste publique — c'est un bouton de formulaire — donc
        # c'est la lecture de champ qui doit fermer la porte.
        with self.assertRaises(AccessError):
            self.env["bf.email.account"].with_user(self.stranger).browse(
                self.account.id).action_refresh_folders()

    def test_writing_into_a_colleague_folder_cache_is_refused(self):
        # ⚠️ Défaut vécu : `store_imap_folders` écrivait en sudo après un
        # simple `ensure_one()` — aucune lecture de champ avant, donc aucune
        # règle d'enregistrement déclenchée, donc un usager pouvait
        # empoisonner l'arborescence affichée à un collègue. Ce test
        # échouait avant que la méthode passe en privé.
        poison = [{"name": "PIÉGÉ", "delimiter": "/",
                   "has_children": False, "noselect": False}]
        account_id = self.account.id
        with self.assertRaises(AccessError):
            self.env["bf.email.account"].with_user(self.stranger).browse(
                account_id)._store_imap_folders(poison)
        self.account.invalidate_recordset()
        self.assertNotIn("PIÉGÉ", self.account.folder_cache or "")

    def test_the_settings_button_only_touches_my_own_accounts(self):
        # Le bouton « Relever mes dossiers » vit sur les réglages ; il ne doit
        # relever que les comptes de qui appuie.
        accounts = self.env["bf.email"].with_user(
            self.stranger)._inbox_imap_accounts()
        self.assertNotIn(self.account.id, accounts.ids)


@tagged("post_install", "-at_install")
class TestSettingsAreAdminOnly(IsolationCase):
    """Le réglage « Dossiers IMAP » est d'organisation, donc réservé.

    Distinct de la case « Administrateur — tous les courriels », qui vit sur
    la fiche de l'usager et ouvre la lecture des boîtes : celui-ci ne fait
    qu'afficher ou retirer un groupe de dossiers, et n'accorde aucun accès.
    """

    def test_a_plain_user_cannot_open_the_settings(self):
        with self.assertRaises(AccessError):
            self.env["res.config.settings"].with_user(
                self.stranger).create({})

    def test_the_email_admin_is_not_a_system_admin(self):
        # Porter « tous les courriels » ne donne pas la main sur les
        # réglages de la base : ce sont deux pouvoirs distincts.
        self.assertFalse(self.email_admin.has_group("base.group_system"))
        with self.assertRaises(AccessError):
            self.env["res.config.settings"].with_user(
                self.email_admin).create({})

    def test_a_system_admin_can_flip_it_both_ways(self):
        ICP = self.env["ir.config_parameter"].sudo()
        sysadmin = self.env["res.users"].with_context(
            no_reset_password=True).create({
                "name": "Admin Système", "login": "sysadmin@test.invalid",
                "groups_id": [(6, 0, [
                    self.env.ref("base.group_user").id,
                    self.env.ref("base.group_system").id,
                ])],
            })
        Settings = self.env["res.config.settings"].with_user(sysadmin)
        Settings.create({"bf_email_show_imap_folders": False}).execute()
        self.assertEqual(
            ICP.get_param("bf_email.show_imap_folders"), "0")
        Settings.create({"bf_email_show_imap_folders": True}).execute()
        self.assertEqual(
            ICP.get_param("bf_email.show_imap_folders"), "1")

    def test_hiding_the_folders_hides_nothing_else(self):
        # Le réglage est un interrupteur d'affichage : couper le groupe ne
        # doit rien retirer d'autre à personne.
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_email.show_imap_folders", "0")
        found = self.as_owner().search([])
        self.assertIn(self.inbound.id, found.ids)
        keys = [f["key"] for f in self.as_owner().inbox_get_folders()]
        self.assertIn("inbox", keys)
        self.assertNotIn("imapfolders", keys)
