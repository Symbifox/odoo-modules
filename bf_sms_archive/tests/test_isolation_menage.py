"""Isolation par personne des SMS et appels.

A et B : internes, non administrateurs, dans `group_sms_user` (jamais
gestionnaire). A tient une ligne privée ; il partage exprès une seconde ligne
avec B (la ligne de la maison). Le partage d'une ligne est voulu ; ce qui ne
l'est pas, c'est que ce qui voyage sur la ligne PRIVÉE de A atteigne B.

⚠️ Un fil est unique par (numéro, propriétaire) : un même contact qui écrit
sur les deux lignes de A remplit UN fil « mélangé », rattaché à la ligne
partagée. C'est ce cas qui fuit.
"""
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install", "isolation_menage")
class TestIsolationMenageSms(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        g = "base.group_user,bf_sms_archive.group_sms_user"
        cls.a = new_test_user(cls.env, login="menage_sms_a", groups=g)
        cls.b = new_test_user(cls.env, login="menage_sms_b", groups=g)
        Line = cls.env["sms.archive.line"]
        cls.ligne_privee = Line.create({"label": "Cell de A (fictif)", "did": "5145550101", "owner_id": cls.a.id})
        cls.ligne_maison = Line.create({"label": "Maison (fictif)", "did": "5145550102", "owner_id": cls.a.id,
                                        "user_ids": [(6, 0, cls.b.ids)]})

    def _en(self, user, model):
        self.env.invalidate_all()
        return self.env[model].with_user(user)

    def _fil(self, phone, line, owner=None):
        return self.env["sms.archive.thread"].create({
            "phone_normalized": phone, "owner_id": (owner or self.a).id, "line_id": line.id})

    def _msg(self, thread, line, body, direction="in"):
        return self.env["sms.archive.message"].create({
            "thread_id": thread.id, "message_hash": f"menage-{thread.id}-{body}",
            "direction": direction, "body": body, "date_sent": "2026-09-01 12:00:00",
            "line_id": line.id if line else False,
        })

    # ------------------------------------------------------------------
    def test_ligne_privee_fermee_a_b(self):
        fil = self._fil("+15145559001", self.ligne_privee)
        msg = self._msg(fil, self.ligne_privee, "Privé fictif")
        appel = self.env["call.archive.call"].create({
            "thread_id": fil.id, "call_hash": "menage-appel-1", "call_type": "incoming",
            "date": "2026-09-01 12:00:00"})
        self.env.invalidate_all()
        for model, rec in (("sms.archive.thread", fil), ("sms.archive.message", msg),
                           ("call.archive.call", appel), ("sms.archive.line", self.ligne_privee)):
            with self.subTest(model=model):
                self.assertFalse(self._en(self.b, model).search([("id", "=", rec.id)]))
                with self.assertRaises(AccessError):
                    self._en(self.b, model).browse(rec.id).read(["display_name"])
                with self.assertRaises(AccessError):
                    self._en(self.b, model).browse(rec.id).check_access("write")

    def test_notification_du_message_prive_ne_va_pas_a_b(self):
        """Fil mélangé : le message arrivé sur la ligne privée de A ne doit
        prévenir que A (bus, WebPush, UnifiedPush, FCM passent tous par
        `_notify_users`)."""
        fil = self._fil("+15145559002", self.ligne_maison)
        sur_maison = self._msg(fil, self.ligne_maison, "Souper à 18 h (fictif)")
        sur_prive = self._msg(fil, self.ligne_privee, "Résultat privé (fictif)")
        self.assertIn(self.b, sur_maison._notify_users(), "contre-épreuve : la ligne partagée prévient B")
        self.assertNotIn(self.b, sur_prive._notify_users(),
                         "le message reçu sur la ligne PRIVÉE de A prévient B")
        self.assertIn(self.a, sur_prive._notify_users())
        # Et la règle de lecture dit la même chose que la notification
        self.env.invalidate_all()
        self.assertFalse(self._en(self.b, "sms.archive.message").search([("id", "=", sur_prive.id)]))

    def test_b_ne_detourne_pas_les_push_de_a(self):
        sub = self._en(self.b, "sms.archive.push.subscription").create({
            "user_id": self.b.id, "endpoint": "https://push.invalid/menage-b",
            "p256dh": "cGs", "auth": "YXV0aA"})
        refuse = False
        try:
            with self.env.cr.savepoint():
                self._en(self.b, "sms.archive.push.subscription").browse(sub.id).write({"user_id": self.a.id})
        except (AccessError, UserError):
            refuse = True
        self.env.invalidate_all()
        self.assertEqual(sub.sudo().user_id, self.b, "B a réattribué son abonnement push à A : "
                                                      "son navigateur recevrait les SMS de A")
        self.assertTrue(refuse)

    def test_b_ne_coupe_pas_la_ligne_ni_le_relais_de_a(self):
        avant = self.ligne_privee.sudo().webhook_token
        with self.assertRaises(AccessError):
            with self.env.cr.savepoint():
                self._en(self.b, "sms.archive.line").browse(self.ligne_privee.id).action_regenerate_token()
        # même sur la ligne PARTAGÉE : B la consulte, il ne la reconfigure pas
        avant_maison = self.ligne_maison.sudo().webhook_token
        with self.assertRaises(AccessError):
            with self.env.cr.savepoint():
                self._en(self.b, "sms.archive.line").browse(self.ligne_maison.id).action_regenerate_token()
        self.env.invalidate_all()
        self.assertEqual(self.ligne_privee.sudo().webhook_token, avant)
        self.assertEqual(self.ligne_maison.sudo().webhook_token, avant_maison)
        dev = self.env["sms.archive.device"].create({"name": "Relais fictif de A", "owner_id": self.a.id})
        jeton = dev.api_token
        with self.assertRaises(AccessError):
            with self.env.cr.savepoint():
                self._en(self.b, "sms.archive.device").browse(dev.id).action_regenerate_token()
        self.env.invalidate_all()
        self.assertEqual(dev.sudo().api_token, jeton, "B a coupé le relais Android de A")

    def test_a_regenere_toujours_son_jeton(self):
        avant = self.ligne_privee.sudo().webhook_token
        self._en(self.a, "sms.archive.line").browse(self.ligne_privee.id).action_regenerate_token()
        self.env.invalidate_all()
        self.assertNotEqual(self.ligne_privee.sudo().webhook_token, avant)

    def test_export_de_a_ne_se_lit_pas_par_b(self):
        fil = self._fil("+15145559003", self.ligne_maison)
        self._msg(fil, self.ligne_maison, "Commun fictif")
        self._msg(fil, self.ligne_privee, "Privé fictif exporté")
        self.env.invalidate_all()
        for action in ("action_export_csv", "action_export_xml"):
            with self.subTest(action=action):
                res = getattr(self._en(self.a, "sms.archive.thread").browse(fil.id), action)()
                att_id = int(res["url"].split("/web/content/")[1].split("?")[0])
                self.env.invalidate_all()
                self.assertTrue(self._en(self.a, "ir.attachment").browse(att_id).read(["name"]),
                                "contre-épreuve : A relit son export")
                self.assertFalse(self._en(self.b, "ir.attachment").search([("id", "=", att_id)]),
                                 "B trouve l'export de A (qui porte les messages de la ligne privée)")
                with self.assertRaises(AccessError):
                    self._en(self.b, "ir.attachment").browse(att_id).read(["datas"])

    def test_b_ne_glisse_pas_un_message_dans_le_fil_confidentiel_de_a(self):
        secret = self._fil("+15145559004", self.ligne_maison)
        secret.is_hidden = True
        fil_b = self._fil("+15145559005", self.ligne_maison, owner=self.b)
        msg_b = self._msg(fil_b, self.ligne_maison, "Message de B")
        self.env.invalidate_all()
        refuse = False
        try:
            with self.env.cr.savepoint():
                self._en(self.b, "sms.archive.message").browse(msg_b.id).write({"thread_id": secret.id})
        except (AccessError, UserError):
            refuse = True
        self.env.invalidate_all()
        self.assertEqual(msg_b.sudo().thread_id, fil_b, "B a glissé son message dans le fil confidentiel de A")
        self.assertTrue(refuse)

    # ------------------------------------------------------------------
    # Une règle par modèle, et un essai qui tombe si on la retire
    # ------------------------------------------------------------------
    def test_fiches_personnelles_fermees(self):
        fil = self._fil("+15145559010", self.ligne_privee)
        msg = self._msg(fil, self.ligne_privee, "MMS fictif")
        recs = [
            self.env["sms.archive.mms.part"].create({"message_id": msg.id, "content_type": "text/plain"}),
            self.env["sms.archive.link"].create({"message_id": msg.id, "res_model": "res.partner",
                                                 "res_id": self.a.partner_id.id}),
            self.env["sms.archive.mobile.device"].create({"user_id": self.a.id, "name": "Téléphone fictif"}),
            self.env["sms.archive.push.subscription"].create({
                "user_id": self.a.id, "endpoint": "https://push.invalid/menage-a", "p256dh": "cGs", "auth": "YQ"}),
        ]
        for rec in recs:
            with self.subTest(model=rec._name):
                self.env.invalidate_all()
                self.assertTrue(self._en(self.a, rec._name).search([("id", "=", rec.id)]), f"{rec._name} : A ne voit plus le sien")
                self.assertFalse(self._en(self.b, rec._name).search([("id", "=", rec.id)]), f"{rec._name} : B voit celui de A")
