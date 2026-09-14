"""Le crochet d'installation lit la BASE, pas seulement le code sur le disque.

🔴 Des fichiers 18.0.1.12.0 copiés sans ``-u`` de ``bf_timesheet_timer`` ont
les méthodes attendues et une base sans la colonne ``first_start`` : un
contrôle sur le code laisse passer une installation qui cassera au premier
chrono.
"""
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from odoo.addons.bf_timesheet_timer_mobile.hooks import _exiger_chrono_fige


@tagged("post_install", "-at_install", "bf_timesheet_timer_mobile")
class TestCrochet(TransactionCase):

    def test_la_base_a_jour_passe(self):
        _exiger_chrono_fige(self.env)

    def test_une_base_restee_en_1_11_est_refusee(self):
        self.env.cr.execute(
            "UPDATE ir_module_module SET latest_version = '18.0.1.11.2' "
            "WHERE name = 'bf_timesheet_timer'")
        with self.assertRaises(UserError):
            _exiger_chrono_fige(self.env)

    def test_une_base_sans_la_colonne_est_refusee(self):
        self.env.cr.execute("ALTER TABLE bf_timer RENAME COLUMN first_start TO first_start_cache")
        try:
            with self.assertRaises(UserError):
                _exiger_chrono_fige(self.env)
        finally:
            self.env.cr.execute(
                "ALTER TABLE bf_timer RENAME COLUMN first_start_cache TO first_start")
