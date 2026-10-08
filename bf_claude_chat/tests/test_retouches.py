"""Retouches de Gen au bureau et au téléphone.

Ce qui doit rester vrai :

- chaque réponse garde le jugement de fermeture de SON tour, et une
  conversation garde le moment et le chemin de son archivage : c'est ce qui
  permet de comparer ce que Gen disait au geste de la personne ;
- sur une fiche, la liste montre tout l'historique, archivées comprises ;
  ailleurs, les archivées seulement quand on les demande ;
- la liste du bureau dit où Gen travaille ; une réponse rechargée garde sa
  consommation ; la conversation porte son total ;
- le forfait passe par le pont, ne garde que ce qu'il sait afficher et se tait
  quand il ne sait rien.
"""

import json
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests.common import HttpCase, new_test_user, tagged

from ..closure import closure_note
# ⚠️ Le module, pas la classe : une classe d'essais importée ici serait
# rejouée une seconde fois sous ce fichier.
from . import test_fermeture as _fermeture
from .test_tours import trame


@tagged("post_install", "-at_install")
class TestJournalDesTours(_fermeture._Base):

    _tour = _fermeture.TestTourAvecFermeture._tour

    def setUp(self):
        super().setUp()
        self.registry.enter_test_mode(self.cr)
        self.addCleanup(self.registry.leave_test_mode)

    def test_chaque_reponse_garde_le_jugement_de_son_tour(self):
        session, message, _p, _e = self._tour([
            trame("done", {"response": 'Brouillon déposé au chatter.\n'
                                       '<closure state="done">brouillon au chatter</closure>'})])
        self.assertEqual(message.closure_state, "done")
        self.assertEqual(message.closure_reason, "brouillon au chatter")
        self.assertEqual(session.closure_state, "done")

    def test_un_tour_en_erreur_garde_ouvert_et_sans_balise_rien(self):
        _s, message, _p, _e = self._tour([
            trame("text", {"delta": "Début"}),
            trame("error", {"reason": "stopped", "response": ""})])
        self.assertEqual(message.closure_state, "open")
        _s, message, _p, _e = self._tour([trame("done", {"response": "Sans balise."})])
        self.assertFalse(message.closure_state)

    def test_reglage_eteint_la_reponse_n_a_pas_de_jugement(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_claude_chat.closure_enabled", "False")
        _s, message, _p, _e = self._tour([
            trame("done", {"response": 'ok <closure state="done">x</closure>'})])
        self.assertFalse(message.closure_state)

    def test_le_jugement_du_tour_ne_se_fabrique_pas(self):
        session = self.env["claude.chat.session"].create(
            {"name": "À moi", "user_id": self.user.id})
        Message = self.env["claude.chat.message"].with_user(self.user)
        with self.assertRaises(AccessError):
            Message.create({"session_id": session.id, "role": "assistant",
                            "content": "x", "closure_state": "done"})
        # Par défaut de contexte : la clé est retirée, rien ne s'écrit.
        cree = Message.with_context(default_closure_state="done").create(
            {"session_id": session.id, "role": "assistant", "content": "x"})
        self.assertFalse(cree.sudo().closure_state)
        message = Message.create({"session_id": session.id, "role": "user", "content": "q"})
        with self.assertRaises(AccessError):
            message.write({"closure_reason": "forgé"})

    def test_la_consigne_juge_la_conversation_et_garde_les_offres(self):
        note = closure_note()
        self.assertIn("pas tout le dossier", note)
        self.assertIn("c'est waiting", note)
        self.assertIn("brouillon au chatter", note)
        self.assertNotIn("but de la personne", note)


@tagged("post_install", "-at_install")
class TestArchivage(_fermeture._Base):

    def _session(self, **vals):
        return self.env["claude.chat.session"].create(
            dict({"name": "Conversation", "user_id": self.user.id}, **vals))

    def test_date_et_chemin_de_l_archivage(self):
        session = self._session()
        session.with_user(self.user).with_context(gen_archive_source="list").write(
            {"active": False})
        self.assertTrue(session.archive_date)
        self.assertEqual(session.archive_source, "list")
        # Revenue : plus rien ne dit qu'elle est archivée.
        session.with_user(self.user).write({"active": True})
        self.assertFalse(session.archive_date)
        self.assertFalse(session.archive_source)
        session.with_user(self.user)._closure_answer("archive")
        self.assertEqual(session.archive_source, "banner")

    def test_sans_chemin_connu_c_est_autre(self):
        for contexte in ({}, {"gen_archive_source": "nimporte"}):
            session = self._session()
            session.with_user(self.user).with_context(**contexte).write({"active": False})
            self.assertEqual(session.archive_source, "other", contexte)
        session = self._session()
        session.with_user(self.user).action_archive()
        self.assertFalse(session.active)
        self.assertEqual(session.archive_source, "other")

    def test_rearchiver_ne_deplace_pas_la_date(self):
        session = self._session()
        session.with_context(gen_archive_source="batch").write({"active": False})
        quand = fields.Datetime.now().replace(year=2026, month=9, day=1)
        session.sudo().write({"archive_date": quand})
        session.with_context(gen_archive_source="list").write({"active": False})
        self.assertEqual(session.archive_date, quand)
        self.assertEqual(session.archive_source, "batch")

    def test_pas_encore_laisse_une_trace(self):
        session = self._session(closure_state="done")
        session.with_user(self.user)._closure_answer("later")
        session.with_user(self.user)._closure_answer("later")
        self.assertEqual(session.closure_later_count, 2)
        self.assertTrue(session.closure_later_date)
        self.assertEqual(session.closure_state, "open")

    def test_le_journal_ne_se_forge_pas_a_la_creation(self):
        """Les défauts s'ajoutent APRÈS le contrôle des
        valeurs, par le contexte ou par un `ir.default` personnel."""
        Session = self.env["claude.chat.session"].with_user(self.user)
        cree = Session.with_context(default_archive_source="banner",
                                    default_closure_state="done").create(
            {"name": "Neuve", "user_id": self.user.id})
        self.assertFalse(cree.sudo().archive_source)
        self.assertFalse(cree.sudo().closure_state)
        self.env["ir.default"].set("claude.chat.session", "closure_later_count", 5,
                                   user_id=self.user.id)
        with self.assertRaises(AccessError):
            Session.create({"name": "Forgée", "user_id": self.user.id})

    def test_les_relances_de_nuit_ne_sont_pas_des_tours(self):
        session = self._session()
        Message = self.env["claude.chat.message"]
        Message.create({"session_id": session.id, "role": "assistant", "content": "r",
                        "input_tokens": 100, "cost_usd": 0.01})
        Message.create({"session_id": session.id, "role": "assistant", "content": "relance",
                        "followup": True})
        self.assertEqual(session._usage_totals()["turns"], 1)

    def test_les_champs_d_archivage_sont_au_serveur(self):
        session = self._session()
        for vals in ({"archive_date": fields.Datetime.now()},
                     {"archive_source": "banner"}, {"closure_later_count": 9}):
            with self.assertRaises(AccessError):
                session.with_user(self.user).write(vals)


@tagged("post_install", "-at_install")
class TestRoutesRetouches(HttpCase):

    def setUp(self):
        super().setUp()
        ICP = self.env["ir.config_parameter"].sudo()
        ICP.set_param("bf_claude_chat.closure_enabled", "True")
        ICP.set_param("bf_ai_bridge.tenant", "bf")
        self.moi = new_test_user(self.env, login="banc_retouches",
                                 password="banc_retouches",
                                 groups="base.group_user,project.group_project_user")
        self.autre = new_test_user(self.env, login="banc_retouches_autre")
        projet = self.env["project.project"].create({
            "name": "Projet retouches", "privacy_visibility": "employees"})
        self.tache = self.env["project.task"].create(
            {"name": "Tâche retouches", "project_id": projet.id})
        self.voisine = self.env["project.task"].create(
            {"name": "Voisine retouches", "project_id": projet.id})
        Session = self.env["claude.chat.session"]
        fiche = {"user_id": self.moi.id, "res_model": "project.task", "res_id": self.tache.id}
        self.vivante = Session.create(dict(fiche, name="Vivante"))
        self.vieille = Session.create(dict(fiche, name="Vieille"))
        self.ancienne = Session.create(dict(fiche, name="Ancienne"))
        (self.vieille | self.ancienne).with_context(gen_archive_source="batch").write(
            {"active": False})
        self.ailleurs = Session.create({"name": "Ailleurs", "user_id": self.moi.id,
                                        "res_model": "project.task",
                                        "res_id": self.voisine.id})
        self.sienne = Session.create(dict(fiche, name="Sienne", user_id=self.autre.id))
        self.sienne_archivee = Session.create({"name": "Sienne archivée",
                                               "user_id": self.autre.id, "active": False})
        self.authenticate("banc_retouches", "banc_retouches")

    def _json(self, route, **params):
        return self.make_jsonrpc_request(route, params)

    def test_sur_une_fiche_tout_l_historique_les_actives_d_abord(self):
        r = self._json("/claude-chat/sessions", res_model="project.task", res_id=self.tache.id)
        ids = [s["id"] for s in r["sessions"]]
        self.assertEqual(ids[0], self.vivante.id)
        self.assertCountEqual(ids, [self.vivante.id, self.vieille.id, self.ancienne.id])
        actives = {s["id"]: s["active"] for s in r["sessions"]}
        self.assertTrue(actives[self.vivante.id])
        self.assertFalse(actives[self.vieille.id])
        # « À suivre » sur une fiche ne ramène pas d'archivée.
        r = self._json("/claude-chat/sessions", res_model="project.task",
                       res_id=self.tache.id, to_follow=True)
        self.assertNotIn(self.vieille.id, [s["id"] for s in r["sessions"]])

    def test_hors_fiche_les_archivees_sur_demande_seulement(self):
        r = self._json("/claude-chat/sessions")
        ids = [s["id"] for s in r["sessions"]]
        self.assertIn(self.vivante.id, ids)
        self.assertNotIn(self.vieille.id, ids)
        r = self._json("/claude-chat/sessions", archived=True)
        ids = [s["id"] for s in r["sessions"]]
        self.assertCountEqual(ids, [self.vieille.id, self.ancienne.id])
        self.assertNotIn(self.sienne_archivee.id, ids)

    def test_la_liste_dit_ou_gen_travaille(self):
        tour = self.env["claude.chat.message"].create({
            "session_id": self.vivante.id, "role": "assistant", "content": "…",
            "state": "pending"})
        tour.sudo().write({"turn_key": "banc:retouches:" + "z" * 20})
        r = self._json("/claude-chat/sessions")
        busy = {s["id"]: s["busy"] for s in r["sessions"]}
        self.assertTrue(busy[self.vivante.id])
        self.assertFalse(busy[self.ailleurs.id])

    def test_une_reponse_relue_garde_sa_consommation_et_le_total(self):
        Message = self.env["claude.chat.message"]
        for entree, sortie, cout in ((1000, 200, 0.05), (3000, 400, 0.10)):
            Message.create({"session_id": self.vieille.id, "role": "user", "content": "q"})
            Message.create({"session_id": self.vieille.id, "role": "assistant",
                            "content": "r", "input_tokens": entree,
                            "output_tokens": sortie, "cost_usd": cout,
                            "duration_ms": 1500})
        r = self._json("/claude-chat/messages", session_id=self.vieille.id)
        reponses = [m for m in r["messages"] if m["role"] == "assistant"]
        self.assertEqual(reponses[0]["net_tokens"], 1200)
        self.assertEqual(reponses[1]["duration_ms"], 1500)
        self.assertEqual(r["totals"]["net_tokens"], 4600)
        self.assertAlmostEqual(r["totals"]["cost_usd"], 0.15)
        self.assertEqual(r["totals"]["turns"], 2)
        self.assertFalse(r["active"])
        self.assertTrue(r["archive_date"])

    def test_archiver_dit_d_ou_vient_le_geste(self):
        self._json("/claude-chat/delete-session", session_id=self.vivante.id, source="banner")
        self.vivante.invalidate_recordset()
        self.assertEqual(self.vivante.archive_source, "banner")
        self._json("/claude-chat/restore-session", session_id=self.vivante.id)
        self.vivante.invalidate_recordset()
        self.assertFalse(self.vivante.archive_source)
        # Le téléphone a sa propre route : ici, une source inconnue est « la liste ».
        self._json("/claude-chat/delete-session", session_id=self.vivante.id, source="mobile")
        self.vivante.invalidate_recordset()
        self.assertEqual(self.vivante.archive_source, "list")
        refus = self._json("/claude-chat/delete-session", session_id=self.sienne.id)
        self.assertEqual(refus.get("error"), "Session not found")
        self.sienne.invalidate_recordset()
        self.assertTrue(self.sienne.active)

    def test_le_forfait_passe_par_le_pont(self):
        appels = []

        def pont(_self, endpoint, payload, timeout=100, headers=None):
            appels.append((endpoint, payload))
            return {"windows": [
                {"key": "five_hour", "utilization": 38.0, "resets_at": "2026-10-08T12:40:00+00:00"},
                {"key": "seven_day", "utilization": 16, "resets_at": None},
                {"key": "seven_day_opus", "utilization": 5.0},
                {"key": "five_hour", "utilization": "illisible"},
            ], "measured_at": "2026-10-08T08:00:00+00:00"}

        with patch.object(type(self.env["bf.ai.bridge"]), "call", pont):
            r = self._json("/claude-chat/usage", fresh=True, session_id=self.vivante.id)
            autre = self._json("/claude-chat/usage", session_id=self.sienne.id)
        self.assertEqual(appels[0], ("/usage", {"tenant": "bf", "fresh": True}))
        self.assertTrue(r["plan"])
        self.assertEqual([(w["key"], w["utilization"]) for w in r["windows"]],
                         [("five_hour", 38.0), ("seven_day", 16)])
        self.assertEqual(r["session_id"], self.vivante.id)
        self.assertIn("totals", r)
        # La conversation d'un autre : pas de total, et rien qui dise qu'elle existe.
        self.assertNotIn("totals", autre)
        self.assertFalse(appels[1][1]["fresh"])

    def test_le_portail_n_a_pas_de_forfait(self):
        portail = new_test_user(self.env, login="banc_portail_retouches",
                                password="banc_portail_retouches", groups="base.group_portal")
        self.assertTrue(portail.share)
        appels = []
        with patch.object(type(self.env["bf.ai.bridge"]), "call",
                          lambda *a, **k: appels.append(a) or {"windows": []}):
            self.authenticate("banc_portail_retouches", "banc_portail_retouches")
            r = self._json("/claude-chat/usage", fresh=True)
        self.assertEqual(r, {"plan": False})
        self.assertFalse(appels)

    def test_sans_releve_le_forfait_se_tait(self):
        def panne(*a, **k):
            raise ConnectionRefusedError()

        with patch.object(type(self.env["bf.ai.bridge"]), "call", panne):
            r = self._json("/claude-chat/usage")
        self.assertEqual(r["windows"], [])
        self.assertEqual(r["error"], "unavailable")
        with patch.object(type(self.env["bf.ai.bridge"]), "call",
                          lambda *a, **k: {"error": "HTTP 401"}):
            r = self._json("/claude-chat/usage")
        self.assertEqual((r["windows"], r["error"]), ([], "unavailable"))


@tagged("post_install", "-at_install")
class TestMobileRetouches(HttpCase):
    """api 9 : le total de la conversation, le forfait, et l'archivage du téléphone."""

    def setUp(self):
        super().setUp()
        Device = next((self.env[m] for m in ("bf.email.mobile.device",
                                             "sms.archive.mobile.device")
                       if m in self.env), None)
        if Device is None:
            self.skipTest("aucun modèle d'appareil mobile sur cette base")
        ICP = self.env["ir.config_parameter"].sudo()
        ICP.set_param("bf_claude_chat.enabled", "True")
        ICP.set_param("bf_ai_bridge.tenant", "bf")
        self.moi = new_test_user(self.env, login="banc_mobile_retouches")
        self.jeton = Device._issue(self.moi.id, name="Banc retouches").device_token
        self.session = self.env["claude.chat.session"].create(
            {"name": "Au téléphone", "user_id": self.moi.id})
        self.env["claude.chat.message"].create({
            "session_id": self.session.id, "role": "assistant", "content": "r",
            "input_tokens": 500, "output_tokens": 100, "cost_usd": 0.02})

    def _appel(self, route, body=None):
        entetes = {"Authorization": "Bearer %s" % self.jeton}
        url = "/bf_claude_chat/mobile/v1" + route
        if body is None:
            return self.url_open(url, headers=entetes, timeout=30)
        entetes["Content-Type"] = "application/json"
        return self.url_open(url, data=json.dumps(body), headers=entetes, timeout=30)

    def test_api_9_total_forfait_et_archivage(self):
        self.assertEqual(self._appel("/ping").json()["api"], 9)
        corps = self._appel(f"/messages?session_id={self.session.id}").json()
        self.assertEqual(corps["totals"]["net_tokens"], 600)
        with patch.object(type(self.env["bf.ai.bridge"]), "call",
                          lambda *a, **k: {"windows": [{"key": "seven_day", "utilization": 42.0}]}):
            forfait = self._appel("/usage?fresh=1").json()
        self.assertEqual(forfait["windows"][0]["utilization"], 42.0)
        self.assertEqual(self._appel("/delete-session",
                                     {"session_id": self.session.id}).status_code, 200)
        self.session.invalidate_recordset()
        self.assertFalse(self.session.active)
        self.assertEqual(self.session.archive_source, "mobile")
        self.assertEqual(self._appel("/usage").status_code, 200)
        sans_jeton = self.url_open("/bf_claude_chat/mobile/v1/usage", timeout=30)
        self.assertEqual(sans_jeton.status_code, 401)

    def test_la_conversation_d_un_autre_est_introuvable(self):
        autre = new_test_user(self.env, login="banc_mobile_autre_retouches")
        sienne = self.env["claude.chat.session"].create({"name": "Sienne", "user_id": autre.id})
        self.assertEqual(self._appel(f"/messages?session_id={sienne.id}").status_code, 404)
