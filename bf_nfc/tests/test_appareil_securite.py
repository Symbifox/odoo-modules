"""Les deux failles du modèle des appareils, relevées par une relecture adverse.

Un gestionnaire pouvait rattacher son téléphone au compte d'un autre, et une
révocation se défaisait d'un clic.
"""
import base64
import hashlib

from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestAppareilSecurite(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.gestion = new_test_user(cls.env, login="appareil-gestion",
                                    groups="base.group_user,bf_nfc.group_nfc_manager")
        verif = "verificateur-securite-assez-long-pour-etre-serieux"
        defi = base64.urlsafe_b64encode(hashlib.sha256(verif.encode()).digest()).decode().rstrip("=")
        Device = cls.env["bf.nfc.device"]
        cls.appareil, cls.jeton = Device._exchange(Device._issue_pending(cls.gestion.id, challenge=defi), verif)

    def test_un_gestionnaire_ne_rattache_pas_son_telephone_a_un_autre(self):
        """🔴 Sinon son jeton agissait avec les droits de l'administrateur."""
        admin = self.env.ref("base.user_admin")
        with self.assertRaises(AccessError):
            self.appareil.with_user(self.gestion).write({"user_id": admin.id})
        with self.assertRaises(AccessError):
            # Même par un appelant qui aurait le droit d'écrire le modèle.
            self.appareil.with_user(self.env.ref("base.user_admin")).write({"user_id": admin.id})
        self.assertEqual(self.env["bf.nfc.device"]._resolve(self.jeton).user_id, self.gestion)

    def test_rallumer_un_appareil_revoque_ne_ressuscite_pas_son_jeton(self):
        self.assertTrue(self.env["bf.nfc.device"]._resolve(self.jeton))
        self.appareil.write({"active": False})
        self.appareil.write({"active": True})
        self.assertFalse(self.env["bf.nfc.device"]._resolve(self.jeton))

    def test_taper_et_executer_ne_s_appellent_pas_par_rpc(self):
        """🔴 Par RPC, un interne choisissait la porte et le compteur d'une
        pastille signée : remise à zéro, les anciens tapotements signés
        redevenaient rejouables."""
        from odoo.service.model import get_public_method
        for modele, methode in (("bf.nfc.tag", "taper"), ("bf.nfc.gesture", "executer")):
            with self.assertRaises(AccessError):
                get_public_method(self.env[modele], methode)

    def test_un_appariement_en_attente_n_est_pas_un_appareil_actif(self):
        code = self.env["bf.nfc.device"]._issue_pending(self.gestion.id, challenge="x")
        attente = self.env["bf.nfc.device"].sudo().with_context(active_test=False).search(
            [("pending_code", "=", code)])
        self.assertTrue(attente)
        self.assertFalse(attente.active)
