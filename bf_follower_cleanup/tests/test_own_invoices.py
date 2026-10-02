import importlib.util
import pathlib
from unittest import SkipTest

from odoo.addons.base.tests.common import new_test_user
from odoo.tests import TransactionCase, tagged

from ..models.mail_followers import PARAM_ALWAYS_REMOVE

# Ces essais touchent `account.move` alors que le module ne dépend que de `mail` :
# ils ne tournent que là où la facturation est installée, et en post_install pour
# que le registre complet soit monté.
@tagged("post_install", "-at_install")
class TestOwnInvoices(TransactionCase):
    """Le cron garde l'abonnement d'un client à SES factures : le portail d'Odoo
    n'affiche une facture qu'à ses abonnés (account_invoice_rule_portal)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if "account.move" not in cls.env:
            raise SkipTest("account n'est pas installé")
        cls.ICP = cls.env["ir.config_parameter"].sudo()
        cls.ICP.set_param(PARAM_ALWAYS_REMOVE, "")
        Partner = cls.env["res.partner"]
        cls.client_co = Partner.create({"name": "Client Essai Inc.", "is_company": True})
        cls.client_contact = Partner.create(
            {"name": "Contact Essai", "email": "contact@client-essai.test", "parent_id": cls.client_co.id}
        )
        cls.portal_user = new_test_user(
            cls.env, login="portail_client_essai", groups="base.group_portal",
            partner_id=cls.client_contact.id,
        )
        cls.other_co = Partner.create({"name": "Autre Société Essai", "is_company": True})
        cls.other_contact = Partner.create(
            {"name": "Tiers Essai", "email": "tiers@autre-essai.test", "parent_id": cls.other_co.id}
        )
        cls.other_portal = new_test_user(
            cls.env, login="portail_tiers_essai", groups="base.group_portal",
            partner_id=cls.other_contact.id,
        )
        cls.vendor = Partner.create({"name": "Fournisseur Essai", "email": "f@fournisseur-essai.test"})

    def _account(self, move_type):
        # Le compte par défaut d'un journal peut être déprécié dans une vraie base.
        kind = "income" if move_type.startswith("out_") else "expense"
        return self.env["account.account"].search([
            ("account_type", "=", kind), ("deprecated", "=", False),
            ("company_ids", "in", self.env.company.id),
        ], limit=1)

    def _line(self, move_type, name="Service", price=100.0):
        return (0, 0, {"name": name, "quantity": 1, "price_unit": price,
                       "account_id": self._account(move_type).id, "tax_ids": [(6, 0, [])]})

    def _move(self, move_type, partner):
        move = self.env["account.move"].create({
            "move_type": move_type,
            "partner_id": partner.id,
            "invoice_date": "2026-09-01",
            "invoice_line_ids": [self._line(move_type)],
        })
        move.action_post()
        return move

    def _run_cron(self):
        self.env["mail.followers"]._cron_remove_non_internal_followers()
        self.env.invalidate_all()

    def test_customer_keeps_its_own_invoice(self):
        inv = self._move("out_invoice", self.client_contact)
        # Odoo abonne le client facturé à la comptabilisation : c'est l'abonnement que le
        # cron effaçait.
        self.assertIn(self.client_contact, inv.message_partner_ids)
        inv.message_subscribe(self.client_co.ids)
        self._run_cron()
        self.assertIn(self.client_contact, inv.message_partner_ids)
        self.assertIn(self.client_co, inv.message_partner_ids)

    def test_customer_keeps_its_own_credit_note(self):
        refund = self._move("out_refund", self.client_contact)
        self._run_cron()
        self.assertIn(self.client_contact, refund.message_partner_ids)

    def test_portal_user_sees_own_invoice_after_cron(self):
        inv = self._move("out_invoice", self.client_contact)
        self._run_cron()
        Move = self.env["account.move"]
        self.assertEqual(Move.with_user(self.portal_user).search([("id", "=", inv.id)]), inv)
        self.assertFalse(Move.with_user(self.other_portal).search([("id", "=", inv.id)]))

    def _legacy_reparent(self, partner, company):
        # Odoo 18 déplace les écritures d'un contact qui change de société
        # (account/models/partner.py, write). Les données héritées, posées hors
        # ORM ou d'avant cette synchronisation, gardent l'ancienne société sur la
        # facture : on les reproduit en SQL.
        self.env.cr.execute(
            "UPDATE res_partner SET parent_id = %s, commercial_partner_id = %s WHERE id = %s",
            (company.id, company.id, partner.id),
        )
        self.env.invalidate_all()

    def test_orm_reparent_moves_the_invoice_with_the_contact(self):
        # Constat sur Odoo, pas sur ce module : la facture suit le contact.
        person = self.env["res.partner"].create({"name": "Particulier Essai", "email": "p@particulier-essai.test"})
        inv = self._move("out_invoice", person)
        person.parent_id = self.client_co
        self.assertEqual(inv.commercial_partner_id, self.client_co)
        self._run_cron()
        self.assertIn(person, inv.message_partner_ids)

    def test_legacy_individual_attached_to_a_company(self):
        # Vu en production : facture émise à un particulier, rattaché depuis à une
        # société sans que la facture suive. La société actuelle du contact compte.
        person = self.env["res.partner"].create({"name": "Particulier Hérité", "email": "h@particulier-essai.test"})
        inv = self._move("out_invoice", person)
        self._legacy_reparent(person, self.client_co)
        self.assertEqual(inv.commercial_partner_id, person)
        self._run_cron()
        self.assertIn(person, inv.message_partner_ids)
        self.assertEqual(
            self.env["account.move"].with_user(self.portal_user).search([("id", "=", inv.id)]), inv
        )

    def test_legacy_contact_moved_to_another_company(self):
        # Donnée héritée : facture de A, contact passé chez B sans que la facture
        # suive. La facture reste à A : les usagers portail de B ne la voient pas.
        mover = self.env["res.partner"].create(
            {"name": "Contact Mobile Essai", "email": "m@client-essai.test", "parent_id": self.client_co.id}
        )
        inv = self._move("out_invoice", mover)
        self._legacy_reparent(mover, self.other_co)
        self.assertEqual(inv.commercial_partner_id, self.client_co)
        self._run_cron()
        self.assertNotIn(mover, inv.message_partner_ids)
        self.assertFalse(
            self.env["account.move"].with_user(self.other_portal).search([("id", "=", inv.id)])
        )

    def test_other_company_follower_still_removed(self):
        inv = self._move("out_invoice", self.client_contact)
        inv.message_subscribe(self.other_contact.ids)
        self._run_cron()
        self.assertNotIn(self.other_contact, inv.message_partner_ids)
        self.assertFalse(
            self.env["account.move"].with_user(self.other_portal).search([("id", "=", inv.id)])
        )

    def test_vendor_bill_follower_still_removed(self):
        bill = self._move("in_invoice", self.vendor)
        self.assertIn(self.vendor, bill.message_partner_ids)
        self._run_cron()
        self.assertNotIn(self.vendor, bill.message_partner_ids)

    def test_always_remove_list_wins(self):
        inv = self._move("out_invoice", self.client_contact)
        self.ICP.set_param(PARAM_ALWAYS_REMOVE, str(self.client_contact.id))
        self._run_cron()
        self.assertNotIn(self.client_contact, inv.message_partner_ids)

    def test_customer_removed_from_other_records(self):
        # L'exception ne vaut que pour ses factures : ailleurs, le client reste retiré.
        self.client_co.message_subscribe(self.client_contact.ids)
        self._run_cron()
        self.assertNotIn(self.client_contact, self.client_co.message_partner_ids)

    def test_same_id_on_another_model_not_spared(self):
        # Un abonnement à un AUTRE modèle dont l'id égale celui d'une de ses factures
        # ne doit pas passer pour un abonnement à la facture. La ligne est posée en SQL :
        # aucun contact ne porte forcément l'id de la facture d'essai.
        inv = self._move("out_invoice", self.client_contact)
        self.env.cr.execute(
            "INSERT INTO mail_followers (res_model, res_id, partner_id) VALUES ('res.partner', %s, %s)",
            (inv.id, self.client_contact.id),
        )
        self._run_cron()
        self.env.cr.execute(
            "SELECT 1 FROM mail_followers WHERE res_model = 'res.partner' AND res_id = %s AND partner_id = %s",
            (inv.id, self.client_contact.id),
        )
        self.assertFalse(self.env.cr.fetchall())

    def test_internal_follower_kept(self):
        inv = self._move("out_invoice", self.client_contact)
        employee = new_test_user(self.env, login="employe_essai_suiveur", groups="base.group_user")
        inv.message_subscribe(employee.partner_id.ids)
        self._run_cron()
        self.assertIn(employee.partner_id, inv.message_partner_ids)

    def _migrate(self):
        path = pathlib.Path(__file__).parent.parent / "migrations" / "18.0.2.2.0" / "end-migrate.py"
        spec = importlib.util.spec_from_file_location("bf_follower_cleanup_end_migrate", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with self.assertLogs("bf_follower_cleanup_end_migrate", level="INFO") as logs:
            mod.migrate(self.env.cr, "18.0.2.1.0")
        self.env.invalidate_all()
        return logs.output[-1]

    def test_migration_counts_and_is_idempotent(self):
        self._migrate()  # la base du banc peut porter des factures à réabonner
        inv1 = self._move("out_invoice", self.client_contact)
        inv2 = self._move("out_invoice", self.client_contact)
        gone = self.env["res.partner"].create({"name": "Ancien Client Essai", "email": "a@ancien-essai.test"})
        old = self._move("out_invoice", gone)
        mover = self.env["res.partner"].create(
            {"name": "Contact Parti Essai", "email": "cp@client-essai.test", "parent_id": self.client_co.id}
        )
        moved = self._move("out_invoice", mover)
        self._legacy_reparent(mover, self.other_co)
        for move, partner in ((inv1, self.client_contact), (inv2, self.client_contact), (old, gone), (moved, mover)):
            move.message_unsubscribe(partner.ids)
        gone.active = False
        self.assertIn("re-subscribed to 2 posted", self._migrate())
        self.assertIn(self.client_contact, inv1.message_partner_ids)
        self.assertIn(self.client_contact, inv2.message_partner_ids)
        self.assertNotIn(mover, moved.message_partner_ids)
        self.assertIn("re-subscribed to 0 posted", self._migrate())

    def test_migration_resubscribes_posted_customer_invoices(self):
        inv = self._move("out_invoice", self.client_contact)
        draft = self.env["account.move"].create({
            "move_type": "out_invoice", "partner_id": self.client_contact.id,
            "invoice_line_ids": [self._line("out_invoice", "Brouillon", 1.0)],
        })
        bill = self._move("in_invoice", self.vendor)
        inv.message_unsubscribe(self.client_contact.ids)
        draft.message_unsubscribe(self.client_contact.ids)
        bill.message_unsubscribe(self.vendor.ids)
        path = pathlib.Path(__file__).parent.parent / "migrations" / "18.0.2.2.0" / "end-migrate.py"
        spec = importlib.util.spec_from_file_location("bf_follower_cleanup_end_migrate", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.migrate(self.env.cr, "18.0.2.1.0")
        self.env.invalidate_all()
        self.assertIn(self.client_contact, inv.message_partner_ids)
        self.assertNotIn(self.client_contact, draft.message_partner_ids)
        self.assertNotIn(self.vendor, bill.message_partner_ids)
