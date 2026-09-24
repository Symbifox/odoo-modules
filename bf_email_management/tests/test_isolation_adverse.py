"""Relecture adverse : identités, jetons d'envoi mobile, fiches filles.

Données inventées ; aucune connexion (un compte IMAP se crée sans se connecter).
"""
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import new_test_user, tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install", "isolation_menage")
class TestIsolationCourrielAdverse(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Logins en forme d'adresse : c'est la seule preuve qu'accepte le semis.
        cls.a = new_test_user(cls.env, login="adv.mel.a@banc.invalid", groups="base.group_user", email="adv.mel.a@banc.invalid")
        cls.b = new_test_user(cls.env, login="adv.mel.b@banc.invalid", groups="base.group_user", email="adv.mel.b@banc.invalid")
        cls.Ident = cls.env["bf.email.identity"]
        cls.Ident.sudo()._sync_from_accounts(cls.a | cls.b)

    def _b(self, model):
        self.env.invalidate_all()
        return self.env[model].with_user(self.b)

    def _ident_b(self, adresse):
        return self.Ident.sudo().with_context(active_test=False).search(
            [("user_id", "=", self.b.id), ("email_normalized", "=", adresse)])

    def test_semis_verifie_le_login(self):
        self.assertTrue(self._ident_b("adv.mel.b@banc.invalid").verified, "contre-épreuve : login = adresse")

    def test_courriel_de_fiche_ne_prouve_rien(self):
        """B pose une adresse sur SA fiche (externe, ou la boîte
        partagée factures@ tenue par le compte IMAP de A) ; rien de vérifié."""
        self.env["bf.email.account"].with_user(self.a).create({
            "name": "factures", "host": "imap.banc.invalid", "port": 993,
            "login": "factures@banc.invalid", "password": "x"})
        for adresse in ("pdg@client-externe.invalid", "factures@banc.invalid"):
            with self.subTest(adresse=adresse):
                with self.env.cr.savepoint() as sp:
                    self._b("res.users").browse(self.b.id).write({"email": adresse})
                    self._b("bf.email.account").create({"name": "b", "host": "imap.banc.invalid",
                                                        "port": 993, "login": "b.boite@banc.invalid",
                                                        "password": "x"})
                    self.env.invalidate_all()
                    self.assertFalse(self._ident_b(adresse).verified, adresse)
                    self.assertNotIn(adresse, self.Ident._usable_for(self.b).mapped("email_normalized"))
                    sp.rollback()

    def test_admin_courriel_verifie_a_la_main(self):
        c = new_test_user(self.env, login="adv_mel_adm", groups="base.group_user,bf_email_management.group_email_admin")
        ident = self.Ident.sudo().create({"user_id": self.b.id, "name": "Alias", "email": "alias.b@banc.invalid"})
        self.assertFalse(ident.verified)
        self.env["bf.email.identity"].with_user(c).browse(ident.id).write({"verified": True})
        self.assertTrue(ident.verified)

    def test_changer_l_adresse_devérifie(self):
        ident = self._ident_b("adv.mel.b@banc.invalid")
        self._b("bf.email.identity").browse(ident.id).write({"email": "adv.mel.a@banc.invalid"})
        self.env.invalidate_all()
        self.assertFalse(ident.verified, "l'identité de B, réécrite à l'adresse de A, reste vérifiée")
        self.assertNotIn("adv.mel.a@banc.invalid",
                         self.Ident._usable_for(self.b).mapped("email_normalized"))

    def test_serveur_sortant_d_une_autre_adresse_refuse(self):
        srv = self.env["ir.mail_server"].create({
            "name": "SMTP de A (essai)", "smtp_host": "smtp.banc.invalid", "smtp_port": 587,
            "from_filter": "adv.mel.a@banc.invalid"})
        ident = self._ident_b("adv.mel.b@banc.invalid")
        with self.assertRaises(ValidationError):
            with self.env.cr.savepoint():
                self._b("bf.email.identity").browse(ident.id).write({"mail_server_id": srv.id})
        libre = self.env["ir.mail_server"].create({"name": "Sans filtre", "smtp_host": "smtp2.banc.invalid"})
        self._b("bf.email.identity").browse(ident.id).write({"mail_server_id": libre.id})

    def test_compte_au_login_d_un_autre_ne_verifie_rien(self):
        for adresse in ("adv.mel.a@banc.invalid", "pdg@client-externe.invalid"):
            with self.subTest(adresse=adresse):
                with self.env.cr.savepoint() as sp:
                    self._b("bf.email.account").create({
                        "name": "faux", "host": "imap.banc.invalid", "port": 993,
                        "login": adresse, "password": "x"})
                    self.env.invalidate_all()
                    self.assertFalse(self._ident_b(adresse).verified)
                    self.assertNotIn(adresse, self.Ident._usable_for(self.b).mapped("email_normalized"))
                    sp.rollback()

    def test_collegue_sans_identite_semee(self):
        n = new_test_user(self.env, login="adv_mel_n", groups="base.group_user", email="adv.mel.n@banc.invalid")
        self._b("bf.email.account").create({"name": "faux", "host": "imap.banc.invalid", "port": 993,
                                            "login": "adv.mel.n@banc.invalid", "password": "x"})
        self.env.invalidate_all()
        self.assertFalse(self._ident_b("adv.mel.n@banc.invalid").verified)
        self.assertTrue(n)

    def test_jetons_d_envoi_mobile_de_a(self):
        self.env["bf.email.mobile.send"].sudo().create({"token": "jeton-a-essai", "user_id": self.a.id})
        self.assertFalse(self._b("bf.email.mobile.send").search_read([("user_id", "=", self.a.id)], ["token"]))
        self.assertFalse(self._b("bf.email.mobile.send").search([("token", "=", "jeton-a-essai")]))

    def test_reponse_d_absence_rattachee_au_repondeur_de_a(self):
        abs_a = self.env["bf.email.absence"].sudo().create({"name": "A", "user_id": self.a.id, "is_template": True,
                                                            "reply_ids": [(0, 0, {"name": "r", "body_html": "<p>a</p>"})]})
        abs_b = self.env["bf.email.absence"].sudo().create({"name": "B", "user_id": self.b.id, "is_template": True,
                                                            "reply_ids": [(0, 0, {"name": "r", "body_html": "<p>b</p>"})]})
        rep_b = abs_b.reply_ids[:1]
        with self.assertRaises(AccessError):
            with self.env.cr.savepoint():
                self._b("bf.email.absence.reply").browse(rep_b.id).write({"absence_id": abs_a.id})
        with self.assertRaises(AccessError):
            with self.env.cr.savepoint():
                self._b("bf.email.absence.reply").create({"absence_id": abs_a.id, "name": "x", "body_html": "<p>x</p>"})
        self.env.invalidate_all()
        self.assertEqual(len(abs_a.reply_ids), 1)

    def test_condition_rattachee_a_la_regle_de_a(self):
        regle_a = self.env["bf.email.rule"].sudo().create({"name": "règle A", "user_id": self.a.id})
        regle_b = self._b("bf.email.rule").create({"name": "règle B"})
        cond_b = self._b("bf.email.rule.condition").create(
            {"rule_id": regle_b.id, "field_name": "subject", "operator": "contains", "value": "x"})
        with self.assertRaises(AccessError):
            with self.env.cr.savepoint():
                self._b("bf.email.rule.condition").browse(cond_b.id).write({"rule_id": regle_a.id})
        with self.assertRaises(AccessError):
            with self.env.cr.savepoint():
                self._b("bf.email.rule.condition").create(
                    {"rule_id": regle_a.id, "field_name": "subject", "operator": "contains", "value": "y"})
        self.env.invalidate_all()
        self.assertFalse(regle_a.condition_ids)

    def test_admin_courriel_reassigne_toujours_une_regle(self):
        """L'administrateur courriel (sans être administrateur système) réassigne toujours une règle."""
        c = new_test_user(self.env, login="adv_mel_c", groups="base.group_user,bf_email_management.group_email_admin")
        self.assertFalse(c.has_group("base.group_system"))
        regle = self.env["bf.email.rule"].with_user(c).create({"name": "règle de C"})
        regle.with_user(c).write({"user_id": self.a.id})
        self.assertEqual(regle.sudo().user_id, self.a)

    def test_adresse_de_fiche_deja_tenue_par_une_autre(self):
        """Même le LOGIN ne vérifie pas quand l'adresse est déjà tenue par une
        autre personne (ici, le courriel de sa fiche) : un administrateur
        courriel tranche."""
        new_test_user(self.env, login="adv_mel_m", groups="base.group_user",
                      email="adv.mel.m@banc.invalid")
        b2 = new_test_user(self.env, login="adv.mel.m@banc.invalid", groups="base.group_user",
                           email="adv.mel.m@banc.invalid")
        self.Ident.sudo()._sync_from_accounts(b2)
        ident = self.Ident.sudo().with_context(active_test=False).search(
            [("user_id", "=", b2.id), ("email_normalized", "=", "adv.mel.m@banc.invalid")])
        self.assertTrue(ident)
        self.assertFalse(ident.verified)

    def test_login_tenu_par_la_boite_imap_d_une_autre(self):
        """Même le login ne vérifie pas une adresse que le compte IMAP d'une autre
        personne tient déjà (boîte partagée) : l'administrateur courriel tranche."""
        self.env["bf.email.account"].sudo().create({
            "name": "partagée", "user_id": self.a.id, "host": "imap.banc.invalid", "port": 993,
            "login": "boite.partagee@banc.invalid", "password": "x"})
        self.Ident.sudo().with_context(active_test=False).search(
            [("email_normalized", "=", "boite.partagee@banc.invalid")]).unlink()
        u = new_test_user(self.env, login="boite.partagee@banc.invalid", groups="base.group_user")
        self.Ident.sudo()._sync_from_accounts(u)
        ident = self.Ident.sudo().with_context(active_test=False).search(
            [("user_id", "=", u.id), ("email_normalized", "=", "boite.partagee@banc.invalid")])
        self.assertTrue(ident)
        self.assertFalse(ident.verified)

    def test_login_deja_declare_par_une_autre_meme_non_verifie(self):
        self.Ident.sudo().create({"user_id": self.a.id, "name": "déclarée par A",
                                  "email": "declaree@banc.invalid"})
        u = new_test_user(self.env, login="declaree@banc.invalid", groups="base.group_user")
        self.Ident.sudo()._sync_from_accounts(u)
        ident = self.Ident.sudo().with_context(active_test=False).search(
            [("user_id", "=", u.id), ("email_normalized", "=", "declaree@banc.invalid")])
        self.assertTrue(ident)
        self.assertFalse(ident.verified)
