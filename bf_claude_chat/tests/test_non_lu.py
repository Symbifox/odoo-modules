"""Ce que la personne a lu, et ce qui reste « à lire »."""

import json

from odoo.tests.common import HttpCase, TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestNonLu(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(cls.env, login="banc_non_lu", groups="base.group_user")
        cls.Session = cls.env["claude.chat.session"].with_user(cls.user)

    def _session(self):
        return self.Session.create({"name": "Conv non lue", "user_id": self.user.id})

    def _message(self, session, role="assistant", **extra):
        return self.env["claude.chat.message"].create(dict(
            {"session_id": session.id, "role": role, "content": "…"}, **extra))

    def _non_lu(self, session):
        ligne = session.read(["name"])
        return self.Session._with_unread(ligne)[0]["unread"]

    def test_une_reponse_de_gen_est_a_lire_puis_lue(self):
        session = self._session()
        self._message(session, role="user")
        self.assertFalse(self._non_lu(session), "sa propre question n'est jamais à lire")
        reponse = self._message(session)
        self.assertTrue(self._non_lu(session))
        session._mark_seen(reponse.id)
        self.assertFalse(self._non_lu(session))
        self._message(session)
        self.assertTrue(self._non_lu(session), "une nouvelle réponse redevient à lire")

    def test_une_consigne_interne_ne_compte_pas(self):
        session = self._session()
        self._message(session, internal=True)
        self.assertFalse(self._non_lu(session))

    def test_le_repere_ne_recule_jamais_ni_ne_depasse_le_dernier(self):
        session = self._session()
        premiere = self._message(session)
        seconde = self._message(session)
        session._mark_seen(seconde.id)
        session._mark_seen(premiere.id)
        self.assertEqual(session.seen_message_id, seconde.id, "ne recule pas")
        session._mark_seen(seconde.id + 10_000)
        self.assertEqual(session.seen_message_id, seconde.id, "un id forgé ne marque pas d'avance")
        troisieme = self._message(session)
        self.assertTrue(self._non_lu(session))
        session._mark_seen()
        self.assertEqual(session.seen_message_id, troisieme.id, "sans id : jusqu'au dernier")

    def test_lire_ne_fait_pas_bouger_la_conversation(self):
        session = self._session()
        self._message(session)
        session.flush_recordset()
        avant = (session.write_date, session.list_date)
        session._mark_seen()
        session.invalidate_recordset()
        self.assertEqual((session.write_date, session.list_date), avant)

    def test_le_repere_ne_s_ecrit_pas_a_la_main(self):
        champ = self.env["claude.chat.session"]._fields["seen_message_id"]
        self.assertTrue(champ.readonly)


@tagged("post_install", "-at_install")
class TestNonLuRoutes(HttpCase):

    def setUp(self):
        super().setUp()
        Device = next((self.env[m] for m in ("bf.email.mobile.device",
                                             "sms.archive.mobile.device")
                       if m in self.env), None)
        if Device is None:
            self.skipTest("aucun modèle d'appareil mobile sur cette base")
        groupe = self.env.ref("base.group_user")
        self.user = self.env["res.users"].create({
            "name": "Banc non-lu", "login": "banc_non_lu_route",
            "groups_id": [(6, 0, groupe.ids)]})
        self.autre = self.env["res.users"].create({
            "name": "Banc non-lu autre", "login": "banc_non_lu_autre",
            "groups_id": [(6, 0, groupe.ids)]})
        self.jeton = Device._issue(self.user.id, name="Banc non-lu").device_token
        self.env["ir.config_parameter"].sudo().set_param("bf_claude_chat.enabled", "True")
        Session = self.env["claude.chat.session"]
        self.mienne = Session.create({"name": "Mienne", "user_id": self.user.id})
        self.sienne = Session.create({"name": "Sienne", "user_id": self.autre.id})
        Message = self.env["claude.chat.message"]
        self.reponse = Message.create({"session_id": self.mienne.id, "role": "assistant", "content": "a"})
        Message.create({"session_id": self.sienne.id, "role": "assistant", "content": "b"})

    def _appel(self, route, body=None):
        entetes = {"Authorization": "Bearer %s" % self.jeton}
        url = "/bf_claude_chat/mobile/v1" + route
        if body is None:
            return self.url_open(url, headers=entetes, timeout=30)
        entetes["Content-Type"] = "application/json"
        return self.url_open(url, data=json.dumps(body), headers=entetes, timeout=30)

    def _rangs(self):
        return {r["id"]: r for r in self._appel("/sessions").json()["sessions"]}

    def test_api_8(self):
        # Au moins 8 : l'api 9 garde tout ce que la 8 apporte.
        self.assertGreaterEqual(self._appel("/ping").json()["api"], 8)

    def test_la_liste_dit_a_lire_et_seen_la_demarque(self):
        self.assertTrue(self._rangs()[self.mienne.id]["unread"])
        self.assertEqual(self._appel("/seen", {"session_id": self.mienne.id,
                                               "message_id": self.reponse.id}).json(), {"ok": True})
        self.assertFalse(self._rangs()[self.mienne.id]["unread"])

    def test_lire_messages_ne_vaut_pas_lecture(self):
        """L'app relit `/messages` en fin de tour même quand on est parti ailleurs."""
        self._appel("/messages?session_id=%d" % self.mienne.id)
        self.assertTrue(self._rangs()[self.mienne.id]["unread"])

    def test_seen_sur_la_conversation_d_un_autre_est_refuse(self):
        reponse = self._appel("/seen", {"session_id": self.sienne.id})
        self.assertEqual(reponse.status_code, 404)
        self.sienne.invalidate_recordset()
        self.assertFalse(self.sienne.seen_message_id)

    def test_ouvrir_au_bureau_vaut_lecture_sauf_le_tour_en_cours(self):
        bureau = new_test_user(self.env, login="banc_non_lu_bureau", groups="base.group_user")
        session = self.env["claude.chat.session"].create({"name": "Bureau", "user_id": bureau.id})
        Message = self.env["claude.chat.message"]
        finie = Message.create({"session_id": session.id, "role": "assistant", "content": "a"})
        Message.create({"session_id": session.id, "role": "assistant", "content": "…",
                        "state": "pending"})
        self.authenticate(bureau.login, bureau.login)
        self.make_jsonrpc_request("/claude-chat/messages", {"session_id": session.id})
        session.invalidate_recordset()
        self.assertEqual(session.seen_message_id, finie.id)
        # La fin d'un tour suivi à l'écran marque le reste.
        self.make_jsonrpc_request("/claude-chat/seen", {"session_id": session.id})
        session.invalidate_recordset()
        self.assertEqual(session.seen_message_id, max(session.message_ids.ids))

