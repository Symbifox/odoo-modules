"""Relecture adverse : garde de fil, start_conversation, aperçu."""
from odoo.exceptions import AccessError, UserError
from odoo.tests import new_test_user, tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install", "isolation_menage")
class TestIsolationSmsAdverse(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        g = "base.group_user,bf_sms_archive.group_sms_user"
        cls.a = new_test_user(cls.env, login="adv_sms_a", groups=g)
        cls.b = new_test_user(cls.env, login="adv_sms_b", groups=g)
        L = cls.env["sms.archive.line"].sudo()
        cls.privee = L.create({"label": "privée A", "did": "5145550601", "owner_id": cls.a.id})
        cls.partagee = L.create({"label": "partagée", "did": "5145550602", "owner_id": cls.a.id,
                                 "user_ids": [(6, 0, cls.b.ids)]})
        T = cls.env["sms.archive.thread"].sudo()
        cls.f_prive = T.create({"phone_normalized": "+15145559601", "owner_id": cls.a.id, "line_id": cls.privee.id})
        cls.f_cache = T.create({"phone_normalized": "+15145559602", "owner_id": cls.a.id,
                                "line_id": cls.partagee.id, "is_hidden": True})
        cls.f_import = T.create({"phone_normalized": "+15145559603", "owner_id": cls.a.id, "active": False})
        cls.f_partage = T.create({"phone_normalized": "+15145559604", "owner_id": cls.a.id, "line_id": cls.partagee.id})
        cls.f_b = T.create({"phone_normalized": "+15145559605", "owner_id": cls.b.id})
        M = cls.env["sms.archive.message"].sudo()
        cls.m_partage = M.create({"thread_id": cls.f_partage.id, "message_hash": "adv-p", "direction": "in",
                                  "body": "partagé A", "date_sent": "2026-09-20 10:00:00", "line_id": cls.partagee.id})
        M.create({"thread_id": cls.f_partage.id, "message_hash": "adv-q", "direction": "in",
                  "body": "SECRET-PRIVE-A", "date_sent": "2026-09-20 11:00:00", "line_id": cls.privee.id})
        M.create({"thread_id": cls.f_import.id, "message_hash": "adv-i", "direction": "in",
                  "body": "SECRET-IMPORT-A", "date_sent": "2026-09-20 12:00:00"})

    def _b(self, model):
        self.env.invalidate_all()
        return self.env[model].with_user(self.b)

    def test_faux_sms_dans_le_fil_prive_de_a_par_la_ligne_partagee(self):
        with self.assertRaises(AccessError):
            self._b("sms.archive.message").create({
                "thread_id": self.f_prive.id, "line_id": self.partagee.id, "message_hash": "adv-x",
                "direction": "in", "body": "faux", "date_sent": "2026-09-21 10:00:00"})

    def test_b_ne_tire_pas_un_message_de_a_dans_son_fil(self):
        with self.assertRaises(AccessError):
            with self.env.cr.savepoint():
                self._b("sms.archive.thread").browse(self.f_b.id).write({"message_ids": [(4, self.m_partage.id)]})
        with self.assertRaises(AccessError):
            with self.env.cr.savepoint():
                self._b("sms.archive.message").browse(self.m_partage.id).write({"thread_id": self.f_b.id})
        self.env.invalidate_all()
        self.assertEqual(self.m_partage.thread_id, self.f_partage)

    def test_start_conversation_sans_oracle_ni_desarchivage(self):
        """Fil d'autrui illisible : jamais rendu, désarchivé ni rattaché ; B
        reçoit SON fil pour ce numéro sur cette ligne."""
        for fil in (self.f_import, self.f_cache, self.f_prive):
            with self.subTest(fil=fil.phone_normalized):
                tid = self._b("sms.archive.thread").start_conversation(self.partagee.id, fil.phone_normalized)
                self.assertNotEqual(tid, fil.id)
                self.assertEqual(self.env["sms.archive.thread"].browse(tid).owner_id, self.b)
        self.env.invalidate_all()
        self.assertFalse(self.f_import.active)
        self.assertFalse(self.f_import.line_id)
        # Contre-épreuve : fil de la ligne partagée, et numéro neuf.
        self.assertEqual(self._b("sms.archive.thread").start_conversation(self.partagee.id, "+15145559604"),
                         self.f_partage.id)
        neuf = self._b("sms.archive.thread").start_conversation(self.partagee.id, "+15145559699")
        self.assertTrue(self._b("sms.archive.thread").browse(neuf).read(["id"]))

    def test_envoi_n_atteint_pas_le_fil_prive_de_a(self):
        """`action_send` depuis la ligne partagée vers le numéro
        d'un fil privé, archivé, sans ligne, de A (VOIP.ms simulé)."""
        from unittest.mock import patch
        Vo = type(self.env["sms.archive.voipms"])
        with patch.object(Vo, "_voipms_send_sms", lambda self, *a, **k: "faux-id"), \
                patch.object(Vo, "_voipms_send_mms", lambda self, *a, **k: "faux-id"):
            self.partagee.sudo().write({"sms_enabled": True})
            mid = self._b("sms.archive.message").action_send(self.partagee.id, "+15145559603", "bonjour")
            mid = mid if isinstance(mid, int) else mid.get("id")
            self.env.invalidate_all()
            self.assertFalse(self.f_import.active, "fil d'import de A désarchivé")
            self.assertFalse(self.f_import.line_id, "fil d'import de A rattaché à la ligne")
            message = self.env["sms.archive.message"].browse(mid)
            self.assertEqual(message.thread_id.owner_id, self.b)
            # Contre-épreuve : l'envoi normal sur le fil partagé de A marche.
            mid2 = self._b("sms.archive.message").action_send(self.partagee.id, "+15145559604", "suite")
            mid2 = mid2 if isinstance(mid2, int) else mid2.get("id")
            self.assertEqual(self.env["sms.archive.message"].browse(mid2).thread_id, self.f_partage)

    def test_apercu_ne_montre_que_ce_que_l_on_lit(self):
        vu = self._b("sms.archive.thread").browse(self.f_partage.id).read(["last_message_preview"])[0]
        self.assertNotIn("SECRET", vu["last_message_preview"] or "")
        self.assertEqual(vu["last_message_preview"], "partagé A")
        self.env.invalidate_all()
        vu_a = self.env["sms.archive.thread"].with_user(self.a).browse(self.f_partage.id).last_message_preview
        self.assertEqual(vu_a, "SECRET-PRIVE-A", "contre-épreuve : A voit son dernier message")
