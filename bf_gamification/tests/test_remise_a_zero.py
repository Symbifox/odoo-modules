from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestRemiseAZero(TransactionCase):
    """La remise à zéro (SQL brut) exige un administrateur."""

    def _compte(self):
        self.env.cr.execute("SELECT COUNT(*) FROM bf_gamification_profile")
        return self.env.cr.fetchone()[0]

    def test_un_usager_ordinaire_ne_remet_rien_a_zero(self):
        employe = new_test_user(self.env, login="gamif_reset_employe", groups="base.group_user")
        self.env.cr.execute("UPDATE bf_gamification_profile SET total_xp = 7")
        touches = self.env.cr.rowcount
        reglages = self.env["res.config.settings"].sudo().create({})
        with self.assertRaises(AccessError):
            reglages.with_user(employe).action_reset_all_progress()
        self.env.cr.execute("SELECT COUNT(*) FROM bf_gamification_profile WHERE total_xp = 7")
        self.assertEqual(self.env.cr.fetchone()[0], touches)

    def test_l_administrateur_remet_a_zero(self):
        reglages = self.env["res.config.settings"].create({})
        reglages.action_reset_all_progress()
        self.env.cr.execute("SELECT COUNT(*) FROM bf_gamification_xp_transaction")
        self.assertEqual(self.env.cr.fetchone()[0], 0)
