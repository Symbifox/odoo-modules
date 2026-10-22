"""Compteurs choisis, pastille de l'icône, bascule de forfait."""

import json
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests.common import HttpCase, TransactionCase, new_test_user, tagged

from ..models.claude_chat_session import usage_shown

AUTRE = "/srv/essai/.claude-autre"


def _fenetres(*_a, **_k):
    return {"windows": [
        {"key": "five_hour", "utilization": 38.0, "resets_at": "2026-10-08T12:40:00+00:00"},
        {"key": "seven_day", "utilization": 16, "resets_at": None},
    ], "measured_at": "2026-10-08T08:00:00+00:00"}


@tagged("post_install", "-at_install")
class TestCompteurs(TransactionCase):

    def setUp(self):
        super().setUp()
        self.ICP = self.env["ir.config_parameter"].sudo()
        for cle in ("plan", "usage", "cost"):
            self.ICP.set_param(f"bf_claude_chat.meter_{cle}_group_id", False)
        self.user = new_test_user(self.env, login="banc_compteurs",
                                  groups="base.group_user")

    def test_par_defaut_tout_est_affiche(self):
        self.assertEqual(self.user._gen_meters_shown(),
                         {"session": True, "week": True, "tokens": True, "cost": True})

    def test_un_groupe_borne_le_compteur(self):
        admin = self.env.ref("base.group_system")
        self.ICP.set_param("bf_claude_chat.meter_plan_group_id", str(admin.id))
        self.ICP.set_param("bf_claude_chat.meter_cost_group_id", str(admin.id))
        montrees = self.user._gen_meters_shown()
        self.assertEqual(montrees, {"session": False, "week": False,
                                    "tokens": True, "cost": False})
        self.assertEqual(self.user._gen_meters_allowed(), {"tokens"})
        # Un réglage illisible vaut « tous », jamais une panne.
        self.ICP.set_param("bf_claude_chat.meter_usage_group_id", "pas un id")
        self.assertTrue(self.user._gen_meters_shown()["tokens"])

    def test_le_choix_de_la_personne(self):
        montrees = self.user._set_gen_meters(["week", "cost", "inconnu", "week"])
        self.assertEqual(self.user.gen_meters, "week,cost")
        self.assertEqual(montrees, {"session": False, "week": True,
                                    "tokens": False, "cost": True})
        # Choisi mais non permis : masqué quand même.
        self.ICP.set_param("bf_claude_chat.meter_cost_group_id",
                           str(self.env.ref("base.group_system").id))
        self.assertFalse(self.user._gen_meters_shown()["cost"])
        # L'écran ne parle que des permis : le choix du coût survit.
        self.user._set_gen_meters(["session"])
        self.assertEqual(self.user.gen_meters, "session,cost")

    def test_le_portail_ne_voit_rien(self):
        portail = new_test_user(self.env, login="banc_compteurs_portail",
                                groups="base.group_portal")
        self.assertEqual(portail._gen_meters_allowed(), set())

    def test_consommation_masquee(self):
        u = {"net_tokens": 1200, "total_tokens": 9000, "input_tokens": 3,
             "cost_usd": 0.42, "duration_ms": 5000, "role": "assistant"}
        tout = {"tokens": True, "cost": True}
        self.assertEqual(usage_shown(u, tout)["net_tokens"], 1200)
        self.assertNotIn("role", usage_shown(u, tout))
        sans_cout = usage_shown(u, {"tokens": True, "cost": False})
        self.assertEqual((sans_cout["net_tokens"], sans_cout["cost_usd"]), (1200, 0.0))
        sans_jetons = usage_shown(u, {"tokens": False, "cost": True})
        self.assertEqual((sans_jetons["net_tokens"], sans_jetons["total_tokens"],
                          sans_jetons["cost_usd"], sans_jetons["duration_ms"]),
                         (0, 0, 0.42, 5000))

    def test_totaux_masques(self):
        session = self.env["claude.chat.session"].create(
            {"name": "Totaux", "user_id": self.user.id})
        self.env["claude.chat.message"].create({
            "session_id": session.id, "role": "assistant", "content": "ok",
            # `net_tokens` est calculé : entrée + cache écrit + sortie.
            "input_tokens": 60, "output_tokens": 40, "cost_usd": 0.5})
        self.assertFalse(session._usage_totals_shown({"tokens": False, "cost": True}))
        totaux = session._usage_totals_shown({"tokens": True, "cost": False})
        self.assertEqual((totaux["net_tokens"], totaux["cost_usd"]), (100, 0.0))


@tagged("post_install", "-at_install")
class TestRoutesCompteurs(HttpCase):

    def setUp(self):
        super().setUp()
        ICP = self.env["ir.config_parameter"].sudo()
        ICP.set_param("bf_ai_bridge.tenant", "bf")
        for cle in ("plan", "usage", "cost"):
            ICP.set_param(f"bf_claude_chat.meter_{cle}_group_id", False)
        self.moi = new_test_user(self.env, login="banc_compteurs_routes",
                                 password="banc_compteurs_routes",
                                 groups="base.group_user,project.group_project_user")
        autre = new_test_user(self.env, login="banc_compteurs_autre")
        projet = self.env["project.project"].create({
            "name": "Projet compteurs", "privacy_visibility": "employees"})
        self.tache = self.env["project.task"].create(
            {"name": "Tâche compteurs", "project_id": projet.id})
        self.voisine = self.env["project.task"].create(
            {"name": "Voisine compteurs", "project_id": projet.id})
        Session = self.env["claude.chat.session"]
        fiche = {"user_id": self.moi.id, "res_model": "project.task", "res_id": self.tache.id}
        self.vivante = Session.create(dict(fiche, name="Vivante"))
        Session.create(dict(fiche, name="Archivée", active=False))
        Session.create({"name": "Ailleurs", "user_id": self.moi.id})
        Session.create(dict(fiche, name="Sienne", user_id=autre.id))
        self.authenticate("banc_compteurs_routes", "banc_compteurs_routes")

    def _json(self, route, **params):
        r = self.url_open(route, data=json.dumps({"params": params}),
                          headers={"Content-Type": "application/json"})
        self.assertEqual(r.status_code, 200)
        return r.json().get("result")

    def test_la_jauge_suit_les_compteurs_et_l_usager(self):
        appels = []

        def pont(_self, endpoint, payload, timeout=100, headers=None):
            appels.append((endpoint, payload))
            return _fenetres()

        with patch.object(type(self.env["bf.ai.bridge"]), "call", pont):
            r = self._json("/claude-chat/usage", fresh=True, session_id=self.vivante.id)
        self.assertEqual(appels, [("/usage", {"tenant": "bf", "fresh": True,
                                              "user_id": self.moi.id})])
        self.assertEqual([w["key"] for w in r["windows"]], ["five_hour", "seven_day"])
        self.assertEqual(r["meters"], {"session": True, "week": True,
                                       "tokens": True, "cost": True})
        self.assertEqual(r["meters_allowed"], ["cost", "session", "tokens", "week"])

        # La personne garde la semaine seule : la session ne sort plus.
        choix = self._json("/claude-chat/meters", meters=["week", "tokens"])
        self.assertEqual(choix["meters"]["session"], False)
        with patch.object(type(self.env["bf.ai.bridge"]), "call", pont):
            r = self._json("/claude-chat/usage", session_id=self.vivante.id)
        self.assertEqual([w["key"] for w in r["windows"]], ["seven_day"])

        # Aucune fenêtre voulue : on ne réveille pas le pont.
        self._json("/claude-chat/meters", meters=["tokens"])
        appels.clear()
        with patch.object(type(self.env["bf.ai.bridge"]), "call", pont):
            r = self._json("/claude-chat/usage")
        self.assertFalse(r["plan"])
        self.assertFalse(appels)
        self.assertEqual(self._json("/claude-chat/meters", meters="tout"), {"error": "invalid"})

    def test_la_pastille(self):
        sur_la_fiche = self._json("/claude-chat/badge", res_model="project.task",
                                  res_id=self.tache.id)
        # Les siennes sur la fiche, archivée comprise ; pas celle d'un autre.
        self.assertEqual(sur_la_fiche, {"count": 2, "scope": "record"})
        # Fiche sans conversation, ou aucune fiche : les actives.
        for params in ({"res_model": "project.task", "res_id": self.voisine.id}, {},
                       {"res_model": "project.task", "res_id": "pas un id"}):
            self.assertEqual(self._json("/claude-chat/badge", **params),
                             {"count": 2, "scope": "active"}, params)


@tagged("post_install", "-at_install")
class TestBasculeDeForfait(TransactionCase):

    def setUp(self):
        super().setUp()
        self.env["ir.config_parameter"].sudo().set_param("bf_ai_bridge.tenant", "bf")
        Compte = self.env["claude.account"]
        self.compte = Compte.search([("config_dir", "=", AUTRE)]) or Compte.create(
            {"name": "Autre compte", "config_dir": AUTRE})
        self.usager = new_test_user(self.env, login="banc_bascule",
                                     groups="base.group_user")
        self.etat = {"config_dir": "", "users": []}
        self.appels = []


    # Méthode liée au cas d'essai : posée sur la classe du pont, elle n'est
    # pas re-liée, et reçoit (endpoint, payload, timeout) tels quels.
    def _pont(self, endpoint, payload, timeout=100, headers=None):
        self.appels.append((endpoint, dict(payload)))
        if payload.get("action") == "set":
            self.etat = {"config_dir": payload["config_dir"], "users": payload["users"]}
            return {"ok": True}
        return dict(self.etat, targets=[AUTRE])

    def test_lire_et_ecrire_la_bascule(self):
        with patch.object(type(self.env["bf.ai.bridge"]), "call", self._pont):
            reglages = self.env["res.config.settings"].create({})
            self.assertFalse(reglages.claude_plan_account_id)
            # Le formulaire écrit les deux champs d'un coup : un seul envoi.
            reglages.write({"claude_plan_account_id": self.compte.id,
                            "claude_plan_user_ids": [(6, 0, self.usager.ids)]})
            self.assertEqual(len([a for a in self.appels if a[1].get("action") == "set"]), 1)
            self.assertEqual(self.etat, {"config_dir": AUTRE, "users": [self.usager.id]})
            relus = self.env["res.config.settings"].create({})
            self.assertEqual(relus.claude_plan_account_id, self.compte)
            self.assertEqual(relus.claude_plan_user_ids, self.usager)
            # Enregistrer le formulaire sans rien changer n'écrit rien au pont.
            self.appels.clear()
            self.env["res.config.settings"].create({
                "claude_plan_account_id": self.compte.id,
                "claude_plan_user_ids": [(6, 0, self.usager.ids)]})
            self.assertEqual([a for a in self.appels if a[1].get("action") == "set"], [])
            # Retour au forfait de l'instance : les usagers ne voyagent pas.
            relus.write({"claude_plan_account_id": False})
        self.assertEqual(self.etat, {"config_dir": "", "users": []})
        self.assertTrue(all(a[1]["tenant"] == "bf" for a in self.appels))

    def test_un_formulaire_perime_ne_retire_pas_la_bascule(self):
        """Ouvert avant une bascule faite ailleurs, puis enregistré pour autre chose."""
        with patch.object(type(self.env["bf.ai.bridge"]), "call", self._pont):
            instantane = self.env["claude.account"]._plan_switch_snapshot()
            self.etat = {"config_dir": AUTRE, "users": []}   # le skill bascule entre-temps
            self.appels.clear()
            self.env["res.config.settings"].create({
                "claude_plan_switch_snapshot": instantane,
                "claude_plan_account_id": False, "claude_plan_user_ids": [(6, 0, [])]})
        self.assertEqual([a for a in self.appels if a[1].get("action") == "set"], [])
        self.assertEqual(self.etat["config_dir"], AUTRE)

    def test_une_bascule_que_l_ecran_ne_sait_pas_montrer(self):
        self.etat = {"config_dir": "/ailleurs/.claude-x", "users": []}
        with patch.object(type(self.env["bf.ai.bridge"]), "call", self._pont):
            etat = self.env["claude.account"]._plan_switch_read()
        self.assertTrue(etat["error"])
        self.etat = {"config_dir": AUTRE, "users": [999999999]}
        with patch.object(type(self.env["bf.ai.bridge"]), "call", self._pont):
            etat = self.env["claude.account"]._plan_switch_read()
        self.assertTrue(etat["error"])

    def test_pont_muet_ne_retire_pas_la_bascule(self):
        def muet(*_a, **_k):
            raise ConnectionRefusedError()

        with patch.object(type(self.env["bf.ai.bridge"]), "call", muet):
            reglages = self.env["res.config.settings"].create({})
            self.assertTrue(reglages.claude_plan_switch_error)
            # Le formulaire renvoie les champs vides lus : aucune écriture, aucune erreur.
            self.env["res.config.settings"].create({
                "claude_plan_account_id": False, "claude_plan_user_ids": [(6, 0, [])]})
            with self.assertRaises(UserError):
                reglages.write({"claude_plan_account_id": self.compte.id})

    def test_le_pont_refuse_un_compte(self):
        def refus(_self, endpoint, payload, timeout=100, headers=None):
            if payload.get("action") == "set":
                return {"error": "This plan is not open to this instance."}
            return {"config_dir": "", "users": [], "targets": [AUTRE]}

        with patch.object(type(self.env["bf.ai.bridge"]), "call", refus):
            reglages = self.env["res.config.settings"].create({})
            with self.assertRaises(UserError):
                reglages.write({"claude_plan_account_id": self.compte.id})

    def test_le_formulaire_charge_la_bascule(self):
        """Le chargement du formulaire passe par `onchange` : sans dépendance
        déclarée, ces champs n'y étaient pas calculés (bloc masqué, au banc)."""
        spec = {"company_id": {}, "claude_plan_switch_available": {},
                "claude_plan_account_id": {"fields": {"display_name": {}}}}
        self.etat = {"config_dir": AUTRE, "users": []}
        with patch.object(type(self.env["bf.ai.bridge"]), "call", self._pont):
            valeurs = self.env["res.config.settings"].onchange({}, [], spec)["value"]
        self.assertTrue(valeurs["claude_plan_switch_available"])
        self.assertEqual(valeurs["claude_plan_account_id"]["id"], self.compte.id)

    def test_sans_forfait_ouvert_le_reglage_se_cache(self):
        with patch.object(type(self.env["bf.ai.bridge"]), "call",
                          lambda *a, **k: {"config_dir": "", "users": [], "targets": []}):
            reglages = self.env["res.config.settings"].create({})
            self.assertFalse(reglages.claude_plan_switch_available)
            self.assertFalse(reglages.claude_plan_switch_error)

    def test_hors_administration_rien_ne_part(self):
        appels = []
        with patch.object(type(self.env["bf.ai.bridge"]), "call",
                          lambda *a, **k: appels.append(a) or {}):
            Compte = self.env["claude.account"].with_user(self.usager)
            self.assertFalse(Compte._plan_switch_read()["account"])
            Compte._plan_switch_write(self.compte, self.usager)
        self.assertFalse(appels)
