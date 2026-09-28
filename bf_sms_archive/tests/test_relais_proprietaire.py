from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install", "bf_sms_archive")
class TestRelaisProprietaire(TransactionCase):
    """Le jeton du relais texto meurt avec l'accès interne du propriétaire."""

    def setUp(self):
        super().setUp()
        self.proprio = new_test_user(self.env, login="proprio_relais_t", groups="base.group_user")
        self.Device = self.env["sms.archive.device"]
        self.appareil = self.Device.create({"name": "Relais", "owner_id": self.proprio.id})
        self.jeton = self.appareil.sudo().api_token

    def test_jeton_valide_pour_un_interne_actif(self):
        self.assertEqual(self.Device._resolve(self.jeton), self.appareil)

    def test_jeton_refuse_si_proprietaire_archive(self):
        self.proprio.active = False
        self.assertFalse(self.Device._resolve(self.jeton))

    def test_jeton_refuse_si_proprietaire_passe_au_portail(self):
        self.proprio.groups_id = [(6, 0, [self.env.ref("base.group_portal").id])]
        self.assertTrue(self.proprio.share)
        self.assertFalse(self.Device._resolve(self.jeton))
