"""Isolation par personne du coffre de tokens.

A et B : internes, non administrateurs, tous deux dans `group_otp_user`.
La lecture est déjà bornée par les règles ; ce qui est éprouvé ici en plus, ce
sont les ÉCRITURES qui déplacent une fiche de B chez A. Odoo ne rejoue pas les
règles d'enregistrement après un `write` : sans garde, B glissait un jeton,
une clé d'accès ou un code de relève dans le coffre de A, ou lui donnait son
propre coffre (A ne pouvait plus en créer un).
"""
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged

CHIFFRE = "vhpN7+X+ut2MHTsYL6BUA7A5F5Rf/aa="
IV = "aXYxMjM0NTY3ODkw"


@tagged("post_install", "-at_install", "isolation_menage")
class TestIsolationMenageOtp(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        g = "base.group_user,bf_otp.group_otp_user"
        cls.a = new_test_user(cls.env, login="menage_otp_a", groups=g)
        cls.b = new_test_user(cls.env, login="menage_otp_b", groups=g)

    def _en(self, user, model):
        self.env.invalidate_all()
        return self.env[model].with_user(user)

    def _coffre_complet(self, user):
        V = self._en(user, "bf.otp.vault")
        vid = V.create_my_vault("c2VsMTIzNDU2Nzg5MDEy", 600000, CHIFFRE, IV)
        self._en(user, "bf.otp.token").save_token({
            "name": f"compte-{user.login}@exemple.invalid", "issuer": "Fictif",
            "secret_cipher": CHIFFRE, "secret_iv": IV})
        self._en(user, "bf.otp.vault").add_credential(f"clé {user.login}", f"cred-{user.login}", "c2Vs", CHIFFRE, IV)
        self._en(user, "bf.otp.vault").add_recovery(f"enveloppe {user.login}", "c2Vs", 600000, CHIFFRE, IV)
        self.env.invalidate_all()
        S = self.env
        return {
            "bf.otp.vault": S["bf.otp.vault"].browse(vid),
            "bf.otp.token": S["bf.otp.token"].search([("user_id", "=", user.id)]),
            "bf.otp.credential": S["bf.otp.credential"].search([("user_id", "=", user.id)]),
            "bf.otp.recovery": S["bf.otp.recovery"].search([("user_id", "=", user.id)]),
        }

    def test_b_ne_voit_rien_du_coffre_de_a(self):
        recs = self._coffre_complet(self.a)
        for model, rec in recs.items():
            with self.subTest(model=model):
                self.assertTrue(rec, f"{model} : rien de semé, l'essai ne prouve rien")
                self.assertFalse(self._en(self.b, model).search([("id", "in", rec.ids)]))
                with self.assertRaises(AccessError):
                    self._en(self.b, model).browse(rec.ids).read(["user_id"])
                with self.assertRaises(AccessError):
                    self._en(self.b, model).browse(rec.ids).unlink()
        self.assertFalse(self._en(self.b, "bf.otp.vault").get_my_vault())
        self.assertFalse(self._en(self.b, "bf.otp.token").load_my_tokens().get("tokens") if isinstance(
            self._en(self.b, "bf.otp.token").load_my_tokens(), dict) else self._en(self.b, "bf.otp.token").load_my_tokens())

    def test_b_ne_glisse_rien_dans_le_coffre_de_a(self):
        coffre_a = self._coffre_complet(self.a)["bf.otp.vault"]
        recs_b = self._coffre_complet(self.b)
        for model in ("bf.otp.token", "bf.otp.credential", "bf.otp.recovery"):
            with self.subTest(model=model):
                refuse = False
                try:
                    with self.env.cr.savepoint():
                        self._en(self.b, model).browse(recs_b[model].ids).write({"vault_id": coffre_a.id})
                except (AccessError, UserError):
                    refuse = True
                self.env.invalidate_all()
                self.assertEqual(
                    self.env[model].search_count([("vault_id", "=", coffre_a.id)]), 1,
                    f"{model} : B a glissé sa fiche dans le coffre de A")
                self.assertTrue(refuse)

    def test_b_ne_donne_pas_son_coffre_a_a(self):
        coffre_b = self._coffre_complet(self.b)["bf.otp.vault"]
        refuse = False
        try:
            with self.env.cr.savepoint():
                self._en(self.b, "bf.otp.vault").browse(coffre_b.id).write({"user_id": self.a.id})
        except (AccessError, UserError):
            refuse = True
        self.env.invalidate_all()
        self.assertEqual(coffre_b.sudo().user_id, self.b, "B a donné son coffre à A : A ne peut plus en créer un")
        self.assertTrue(refuse)
        # A peut toujours poser le sien
        self._en(self.a, "bf.otp.vault").create_my_vault("c2Vs", 600000, CHIFFRE, IV)

    def test_a_garde_la_main_sur_son_coffre(self):
        """Contre-épreuve : la garde ne gêne pas les gestes de la personne."""
        recs = self._coffre_complet(self.a)
        tok = recs["bf.otp.token"]
        self._en(self.a, "bf.otp.token").browse(tok.id).write({"issuer": "Autre", "vault_id": recs["bf.otp.vault"].id})
        self._en(self.a, "bf.otp.vault").browse(recs["bf.otp.vault"].id).write({"user_id": self.a.id})
        self.assertEqual(tok.sudo().issuer, "Autre")

    def test_appareil_de_a_ferme_a_b(self):
        dev = self.env["bf.otp.device"].create({"user_id": self.a.id, "name": "Téléphone fictif de A"})
        self.env.invalidate_all()
        self.assertTrue(self._en(self.a, "bf.otp.device").search([("id", "=", dev.id)]))
        self.assertFalse(self._en(self.b, "bf.otp.device").search([("id", "=", dev.id)]))
