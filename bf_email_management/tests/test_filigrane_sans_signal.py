from unittest.mock import patch

from odoo import fields
from odoo.tests import TransactionCase, tagged

from odoo.addons.bf_email_management.models.bf_email import _read_watermark, _write_watermark

KEY = "bf_email.last_sync_date"


@tagged("post_install", "-at_install")
class TestFiligraneSansSignal(TransactionCase):
    """bf_email.last_sync_date ne vide plus le cache de tous les processus."""

    def test_write_then_read_without_cache_signal(self):
        registry = self.env.registry
        registry.cache_invalidated.clear()
        _write_watermark(self.env, KEY, "2031-01-02 03:04:05")
        self.assertEqual(_read_watermark(self.env, KEY), "2031-01-02 03:04:05")
        self.assertNotIn("default", registry.cache_invalidated)
        # L'enregistrement de l'écran Paramètres système voit la même valeur.
        param = self.env["ir.config_parameter"].sudo().search([("key", "=", KEY)])
        self.assertEqual(param.value, "2031-01-02 03:04:05")

    def test_read_sees_a_pending_orm_write(self):
        self.env["ir.config_parameter"].sudo().set_param(KEY, "2030-05-06 07:08:09")
        self.assertEqual(_read_watermark(self.env, KEY), "2030-05-06 07:08:09")

    def test_creates_a_missing_key(self):
        key = "bf_email.test_filigrane_absent"
        self.assertFalse(_read_watermark(self.env, key))
        _write_watermark(self.env, key, "x")
        self.assertEqual(_read_watermark(self.env, key), "x")

    def test_a_real_sync_advances_without_signal(self):
        """Un message neuf fait avancer le filigrane, sans signal de cache."""
        now = fields.Datetime.to_string(self.env.cr.now())
        avant = fields.Datetime.to_string(
            fields.Datetime.subtract(self.env.cr.now(), seconds=1))
        _write_watermark(self.env, KEY, avant)
        self.env["mail.message"].sudo().create({
            "model": "res.partner",
            "res_id": self.env.ref("base.user_admin").partner_id.id,
            "message_type": "email",
            "body": "<p>essai</p>",
            "subject": "essai du filigrane",
            "author_id": self.env.ref("base.user_admin").partner_id.id,
        })
        registry = self.env.registry
        registry.cache_invalidated.clear()
        self.env["bf.email"]._cron_sync_emails()
        self.assertEqual(_read_watermark(self.env, KEY), now)
        self.assertNotIn("default", registry.cache_invalidated)

    def test_sync_now_reads_the_fresh_watermark(self):
        """Le bouton « Synchroniser maintenant » boucle tant que le filigrane avance.

        Il le lisait par get_param, dont le cache ne voit plus les écritures
        de la tâche : il s'arrêtait après un seul lot.
        """
        _write_watermark(self.env, KEY, "2030-01-01 00:00:00")
        self.env["ir.config_parameter"].sudo().get_param(KEY)  # valeur mise en cache
        pas = iter(["2030-01-01 00:00:01", "2030-01-01 00:00:02", "2030-01-01 00:00:03"])
        appels = []

        def tache(bf_email):
            appels.append(1)
            suivant = next(pas, None)
            if suivant:
                _write_watermark(bf_email.env, KEY, suivant)

        BfEmail = type(self.env["bf.email"])
        with patch.object(BfEmail, "_cron_sync_emails", tache), \
                patch.object(BfEmail, "_sync_own_accounts", lambda bf_email: None):
            action = self.env["bf.email"].action_sync_now()
        self.assertEqual(len(appels), 4, "trois lots qui avancent, puis un lot sans avance")
        self.assertIn("2030-01-01 00:00:03", action["params"]["message"])
