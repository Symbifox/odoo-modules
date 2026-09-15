"""Un tour qui survit à son écran : ce qui doit rester vrai sans vrai pont.

Le pont est remplacé par une suite de flux écrits à la main, un par appel :
chaque essai dit ce que le pont rend, puis lit ce que le fil a ENREGISTRÉ.
C'est l'enregistrement qui manquait au bureau, pas l'affichage.
"""

import json
from contextlib import ExitStack
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests.common import HttpCase, TransactionCase, tagged

from ..controllers import turns


def trame(event, data):
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()


class FauxPont:
    """Rend un flux par appel, dans l'ordre, et garde ce qu'on lui a demandé."""

    def __init__(self, *flux):
        self.flux = list(flux)
        self.appels = []

    def __call__(self, socket_path, endpoint, payload, timeout, headers=None):
        self.appels.append((endpoint, dict(payload)))
        suivant = self.flux.pop(0)
        if isinstance(suivant, BaseException):
            raise suivant
        return self._gen(suivant)

    @staticmethod
    def _gen(elements):
        for element in elements:
            if isinstance(element, BaseException):
                raise element
            yield element


class Enregistreur:
    def __init__(self):
        self.recu = []
        self.alive = True

    def put(self, data):
        self.recu.append(data)

    def close(self):
        self.alive = False

    def evenements(self):
        brut = b"".join(self.recu)
        return [e for _m, evs in turns.iter_events([brut]) for e, _d in evs]


@tagged("post_install", "-at_install")
class TestDecisionDeReprise(TransactionCase):

    def test_les_fins_propres_reprennent(self):
        for final in (
            {"_event": "error", "reason": "timeout", "response": "partiel"},
            {"_event": "error", "reason": "max_turns", "response": ""},
            {"_event": "error", "reason": "unknown_turn"},
            {"_event": "error", "reason": "bridge_lost"},
            {"_event": "error", "reason": "cli_error",
             "response": "API Error: 529 Overloaded. This is a server-side issue"},
            {"_event": "error", "reason": "cli_error", "response": ""},
        ):
            self.assertTrue(turns.continue_reason(final), final)

    def test_ce_qui_ne_se_regle_pas_en_recommencant(self):
        for final in (
            {"_event": "done", "response": "ok"},
            {"_event": "error", "reason": "stopped"},
            # Le pont marque toute erreur de résultat du CLI « interrupted ».
            {"_event": "error", "reason": "cli_error", "interrupted": True,
             "response": "You've hit your session limit · resets 10pm"},
            {"_event": "error", "reason": "cli_error", "interrupted": True,
             "response": "No conversation found with session ID abc"},
            {"_event": "error", "reason": "cli_error", "interrupted": True,
             "response": "Prompt is too long"},
            {"_event": "error", "reason": "rate_limit", "response": "Trop de requêtes"},
        ):
            self.assertFalse(turns.continue_reason(final), final)

    def test_un_arret_voulu_l_emporte(self):
        self.assertFalse(turns.continue_reason(
            {"_event": "error", "reason": "timeout"}, stopped=True))

    def test_les_evenements_se_recollent_d_un_morceau_a_l_autre(self):
        brut = trame("text", {"delta": "Bonjour"}) + trame("done", {"response": "Bonjour"})
        morceaux = [brut[:9], brut[9:31], brut[31:]]
        vus = [e for _m, evs in turns.iter_events(morceaux) for e in evs]
        self.assertEqual([e for e, _d in vus], ["text", "done"])
        self.assertEqual(vus[0][1]["delta"], "Bonjour")


@tagged("post_install", "-at_install")
class TestFilDuTour(TransactionCase):

    def setUp(self):
        super().setUp()
        self.registry.enter_test_mode(self.cr)
        self.addCleanup(self.registry.leave_test_mode)
        ICP = self.env["ir.config_parameter"].sudo()
        ICP.set_param("bf_ai_bridge.tenant", "bf")
        ICP.set_param("bf_claude_chat.tenant", "bf")
        self.session = self.env["claude.chat.session"].create({
            "name": "New Chat", "user_id": self.env.uid,
            "claude_session_id": "bf:ancien"})
        self.env["claude.chat.message"].create({
            "session_id": self.session.id, "role": "user", "content": "Ma question"})
        self.message = self.env["claude.chat.message"].create({
            "session_id": self.session.id, "role": "assistant", "content": "…",
            "state": "pending"})
        self.message.sudo().write({
            "turn_key": "banc:1:" + "x" * 20,
            "turn_payload": json.dumps({"message": "Ma question", "tenant": "bf",
                                        "session_id": "bf:ancien",
                                        "user_id": self.env.uid}),
            "runner_heartbeat": fields.Datetime.now(),
        })
        self.env.flush_all()

    def lancer(self, pont, **kwargs):
        ecoute = Enregistreur()
        with ExitStack() as pile:
            pile.enter_context(patch.object(turns.transport, "stream", pont))
            pile.enter_context(patch.object(turns, "_wait_for_bridge", lambda *a, **k: True))
            turns.run_turn(self.cr.dbname, self.message.id, "/nulle/part.sock", 5,
                           listener=ecoute, **kwargs)
        self.env.invalidate_all()
        return ecoute

    def test_une_fin_normale_est_enregistree_avec_ses_etapes(self):
        pont = FauxPont([
            trame("meta", {"session_id": "bf:neuf"}),
            trame("tool", {"name": "Bash", "status": "start"}),
            trame("tool_detail", {"name": "Bash", "detail": "Lire la tâche"}),
            trame("text", {"delta": "Voici "}),
            trame("done", {"session_id": "bf:neuf", "response": "Voici la réponse.",
                           "usage": {"input_tokens": 5, "output_tokens": 7,
                                     "duration_ms": 1200}}),
        ])
        ecoute = self.lancer(pont)
        self.assertEqual(self.message.state, "done")
        self.assertEqual(self.message.content, "Voici la réponse.")
        self.assertEqual(self.message.output_tokens, 7)
        outils = json.loads(self.message.tool_log)
        self.assertEqual((outils[0]["name"], outils[0]["detail"]), ("Bash", "Lire la tâche"))
        self.assertEqual(self.session.claude_session_id, "bf:neuf")
        self.assertEqual(self.session.name, "Ma question")
        self.assertIn("final", ecoute.evenements())
        self.assertEqual(ecoute.evenements()[-1], "saved")
        self.assertFalse(ecoute.alive)
        # Le départ porte la clé du tour et le plafond, pas la clé d'API stockée.
        endpoint, charge = pont.appels[0]
        self.assertEqual(endpoint, "/chat-stream")
        self.assertEqual(charge["turn_key"], self.message.sudo().turn_key)
        self.assertEqual(charge["wall_seconds"], turns.DEFAULT_WALL_SECONDS)

    def test_un_delai_depasse_reprend_seul_sur_la_meme_conversation(self):
        pont = FauxPont(
            [trame("meta", {"session_id": "bf:neuf"}),
             trame("text", {"delta": "Partie un."}),
             trame("error", {"session_id": "bf:neuf", "reason": "timeout",
                             "response": "Partie un.", "interrupted": True})],
            [trame("meta", {"session_id": "bf:neuf"}),
             trame("text", {"delta": "Partie deux."}),
             trame("done", {"session_id": "bf:neuf", "response": "Partie deux."})],
        )
        ecoute = self.lancer(pont)
        self.assertEqual(self.message.state, "done")
        self.assertEqual(self.message.content, "Partie un.\n\nPartie deux.")
        self.assertEqual(self.message.auto_continue_count, 1)
        self.assertIn("resume", ecoute.evenements())
        _endpoint, reprise = pont.appels[1]
        self.assertIn("cut off", reprise["message"])
        self.assertEqual(reprise["session_id"], "bf:neuf")
        self.assertNotEqual(reprise["turn_key"], pont.appels[0][1]["turn_key"])

    def test_les_reprises_s_arretent_au_plafond(self):
        coupe = [trame("meta", {"session_id": "bf:neuf"}),
                 trame("text", {"delta": "encore"}),
                 trame("error", {"reason": "timeout", "interrupted": True})]
        pont = FauxPont(coupe, coupe, coupe)
        self.lancer(pont, max_continue=2)
        self.assertEqual(len(pont.appels), 3)
        self.assertEqual(self.message.auto_continue_count, 2)
        self.assertEqual(self.message.state, "error")
        self.assertEqual(self.message.end_reason, "timeout")
        self.assertEqual(self.session.stream_fail_count, 1)

    def test_le_bouton_arreter_ne_reprend_pas_et_n_empoisonne_pas(self):
        self.message.sudo().write({"stop_requested": True})
        self.env.flush_all()
        pont = FauxPont([
            trame("meta", {"session_id": "bf:neuf"}),
            trame("text", {"delta": "Début"}),
            trame("error", {"reason": "stopped", "response": "", "interrupted": True}),
        ])
        self.lancer(pont)
        self.assertEqual(len(pont.appels), 1)
        self.assertEqual(self.message.state, "error")
        self.assertEqual(self.message.end_reason, "stopped")
        self.assertEqual(self.message.content, "Début")
        self.assertEqual(self.session.stream_fail_count, 0)

    def test_un_arret_demande_pendant_le_tour_empeche_la_reprise(self):
        """Le pont n'a pas reçu l'arrêt (pont ancien, annulation perdue) et
        finit le tour sur un délai : le drapeau posé en base doit suffire."""
        def pont(*args, **kwargs):
            self.cr.execute("UPDATE claude_chat_message SET stop_requested = true WHERE id = %s",
                            (self.message.id,))
            return iter([trame("meta", {"session_id": "bf:neuf"}),
                         trame("text", {"delta": "Début"}),
                         trame("error", {"reason": "timeout", "interrupted": True})])
        appels = []

        def compte(*args, **kwargs):
            appels.append(args[1])
            return pont()
        self.lancer(compte)
        self.assertEqual(appels, ["/chat-stream"])
        self.assertEqual(self.message.end_reason, "stopped")

    def test_une_limite_d_abonnement_ne_reprend_pas(self):
        pont = FauxPont([
            trame("meta", {"session_id": "bf:neuf"}),
            trame("error", {"reason": "cli_error", "interrupted": True,
                            "response": "You've hit your session limit · resets 10pm"}),
        ])
        self.lancer(pont)
        self.assertEqual(len(pont.appels), 1)
        self.assertIn("session limit", self.message.content)

    def test_un_pont_perdu_se_rattache_sans_doubler_le_texte(self):
        pont = FauxPont(
            [trame("meta", {"session_id": "bf:neuf"}),
             trame("text", {"delta": "Même texte"}),
             ConnectionResetError("coupure")],
            [trame("turn", {"turn_key": "k", "offset": 0}),
             trame("meta", {"session_id": "bf:neuf"}),
             trame("text", {"delta": "Même texte"}),
             trame("done", {"response": "Même texte, fini."})],
        )
        self.lancer(pont)
        self.assertEqual([a[0] for a in pont.appels], ["/chat-stream", "/chat-attach"])
        self.assertEqual(pont.appels[1][1]["turn_key"], pont.appels[0][1]["turn_key"])
        self.assertEqual(self.message.content, "Même texte, fini.")
        self.assertEqual(self.message.auto_continue_count, 0)

    def test_un_depart_jamais_recu_renvoie_la_question_pas_la_consigne(self):
        pont = FauxPont(
            ConnectionRefusedError("pont redémarré"),
            [trame("error", {"reason": "unknown_turn", "interrupted": True})],
            [trame("meta", {"session_id": "bf:neuf"}),
             trame("done", {"response": "Réponse."})],
        )
        self.lancer(pont)
        self.assertEqual([a[0] for a in pont.appels],
                         ["/chat-stream", "/chat-attach", "/chat-stream"])
        self.assertEqual(pont.appels[2][1]["message"], "Ma question")
        self.assertEqual(self.message.content, "Réponse.")

    def test_un_fil_repris_relit_le_tour_depuis_le_pont(self):
        self.message.sudo().write({
            "content": "Avant.\n\nDébut du tour en cours",
            "prefix_len": len("Avant.\n\n"),
            "auto_continue_count": 1,
            "tool_log": json.dumps([{"name": "Read", "at": 0, "attempt": 0},
                                    {"name": "Bash", "at": 9, "attempt": 1}]),
        })
        self.env.flush_all()
        pont = FauxPont([
            trame("meta", {"session_id": "bf:neuf"}),
            trame("tool", {"name": "Bash"}),
            trame("text", {"delta": "Début du tour en cours, fini."}),
            trame("done", {"response": "Début du tour en cours, fini."}),
        ])
        self.lancer(pont, attach=True)
        self.assertEqual(pont.appels[0][0], "/chat-attach")
        self.assertEqual(self.message.content, "Avant.\n\nDébut du tour en cours, fini.")
        # L'étape du tour en cours n'est pas comptée deux fois.
        self.assertEqual([t["name"] for t in json.loads(self.message.tool_log)],
                         ["Read", "Bash"])

    def test_un_seul_fil_prend_un_tour_orphelin(self):
        self.cr.execute(
            "UPDATE claude_chat_message SET runner_heartbeat = %s WHERE id = %s",
            (fields.Datetime.now() - timedelta(minutes=5), self.message.id))
        self.assertTrue(turns.claim(self.cr.dbname, self.message.id, 90))
        self.assertFalse(turns.claim(self.cr.dbname, self.message.id, 90))

    def test_un_tour_vivant_ne_se_prend_pas(self):
        self.assertFalse(turns.claim(self.cr.dbname, self.message.id, 90))

    def test_un_tour_sans_charge_de_son_proprietaire_est_refuse(self):
        """🔴 Relecture adverse : un message « en cours » fabriqué, ou dont la
        charge nomme un autre usager, ne doit jamais partir au pont."""
        autre = self.env["res.users"].create({"name": "Autre", "login": "autre_tours"})
        for charge in ({}, {"message": "x", "tenant": "bf", "user_id": autre.id},
                       {"message": "x", "tenant": "bf"}):
            self.message.sudo().write({"state": "pending",
                                       "turn_payload": json.dumps(charge)})
            self.env.flush_all()
            pont = FauxPont()
            self.lancer(pont)
            self.assertEqual(pont.appels, [], charge)
            self.assertEqual((self.message.state, self.message.end_reason),
                             ("error", "invalid"), charge)
            self.assertFalse(self.message.sudo().turn_payload)

    def test_le_locataire_vient_des_parametres_pas_de_la_charge(self):
        """Seconde relecture adverse : une charge forgée ne choisit pas les
        outils du pont."""
        self.message.sudo().write({"turn_payload": json.dumps(
            {"message": "Ma question", "tenant": "demo", "user_id": self.env.uid})})
        self.env.flush_all()
        pont = FauxPont([trame("meta", {"session_id": "bf:neuf"}),
                         trame("done", {"response": "ok"})])
        self.lancer(pont)
        self.assertEqual(pont.appels[0][1]["tenant"], "bf")

    def test_sans_locataire_regle_rien_ne_part(self):
        ICP = self.env["ir.config_parameter"].sudo()
        ICP.set_param("bf_ai_bridge.tenant", False)
        ICP.set_param("bf_claude_chat.tenant", False)
        self.env.flush_all()
        pont = FauxPont()
        self.lancer(pont)
        self.assertEqual(pont.appels, [])
        self.assertEqual(self.message.end_reason, "invalid")

    def test_un_tour_deja_enregistre_n_est_pas_ecrase(self):
        """Deux fils sur la même clé qui voient tous deux la fin : le second ne
        réécrit rien (et ne prévient pas le téléphone une seconde fois)."""
        self.message.sudo().write({"state": "done", "content": "Première écriture"})
        self.env.flush_all()
        pont = FauxPont([trame("meta", {"session_id": "bf:neuf"}),
                         trame("done", {"response": "Seconde écriture"})])
        self.lancer(pont)
        self.assertEqual(self.message.content, "Première écriture")

    def test_la_cle_du_tour_n_atteint_pas_l_ecran(self):
        pont = FauxPont([
            trame("turn", {"turn_key": "secret-du-pont-123456", "offset": 0}),
            trame("meta", {"session_id": "bf:neuf"}),
            trame("done", {"response": "ok"}),
        ])
        ecoute = self.lancer(pont)
        self.assertNotIn(b"secret-du-pont", b"".join(ecoute.recu))
        self.assertNotIn("turn", ecoute.evenements())

    def test_un_fil_qui_perd_la_main_ne_relance_rien(self):
        """Pendant qu'il attendait, un autre fil a pris le tour et changé sa
        clé : celui-ci ne doit pas envoyer une seconde consigne de reprise."""
        cr = self.cr
        message_id = self.message.id

        def coupe():
            yield trame("meta", {"session_id": "bf:neuf"})
            yield trame("text", {"delta": "Début"})
            cr.execute("UPDATE claude_chat_message SET turn_key = %s WHERE id = %s",
                       ("banc:1:" + "z" * 20, message_id))
            yield trame("error", {"reason": "timeout", "interrupted": True})

        pont = FauxPont(coupe())
        self.lancer(pont)
        self.assertEqual(len(pont.appels), 1)
        self.assertEqual(self.message.state, "pending")
        self.assertEqual(self.message.auto_continue_count, 0)

    def test_le_cron_reprend_les_orphelins_et_clot_les_anciens(self):
        ancien = self.env["claude.chat.message"].create({
            "session_id": self.session.id, "role": "assistant", "content": "…",
            "state": "pending"})
        self.env.flush_all()
        self.cr.execute(
            "UPDATE claude_chat_message SET write_date = %s WHERE id = %s",
            (fields.Datetime.now() - timedelta(hours=2), ancien.id))
        self.cr.execute(
            "UPDATE claude_chat_message SET runner_heartbeat = %s WHERE id = %s",
            (fields.Datetime.now() - timedelta(minutes=5), self.message.id))
        self.env.invalidate_all()
        with patch.object(turns, "resume_detached") as reprise:
            self.env["claude.chat.message"]._recover_stale_turns()
        self.assertEqual(reprise.call_args[0][1], self.message)
        self.env.invalidate_all()
        self.assertEqual(ancien.state, "error")
        self.assertEqual(ancien.end_reason, "orphan")


@tagged("post_install", "-at_install")
class TestRoutesDuTour(HttpCase):

    def setUp(self):
        super().setUp()
        self.user = self.env["res.users"].create({
            "name": "Banc Gen", "login": "banc_gen_tours", "password": "banc_gen_tours",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id])],
        })
        self.env["ir.config_parameter"].sudo().set_param("bf_claude_chat.enabled", "True")
        self.fil = self.env["claude.chat.session"].create({
            "name": "Essai", "user_id": self.user.id})
        self.pending = self.env["claude.chat.message"].create({
            "session_id": self.fil.id, "role": "assistant", "content": "…",
            "state": "pending", "client_token": "jetonbanc0001"})
        self.pending.sudo().write({"turn_key": "banc:2:" + "y" * 20,
                                   "runner_heartbeat": fields.Datetime.now()})
        self.authenticate("banc_gen_tours", "banc_gen_tours")

    def sse(self, url, body):
        reponse = self.url_open(url, data=json.dumps(body), headers={
            "Content-Type": "application/json", "X-Claude-Stream": "1"}, timeout=30)
        return [(e, d) for _m, evs in turns.iter_events([reponse.content]) for e, d in evs]

    def test_une_question_pendant_un_tour_s_y_rattache(self):
        avant = self.env["claude.chat.message"].search_count(
            [("session_id", "=", self.fil.id)])
        with patch.object(turns.transport, "stream", return_value=iter([
                trame("done", {"response": "fini"})])), \
                patch("odoo.addons.bf_claude_chat.controllers.main._WATCH_POLLS", 1):
            evenements = self.sse("/claude-chat/stream", {
                "session_id": self.fil.id, "message": "Continue"})
        noms = [e for e, _d in evenements]
        self.assertIn("busy", noms)
        self.assertEqual(dict(evenements)["gen_turn"]["turn_id"], self.pending.id)
        self.assertEqual(avant, self.env["claude.chat.message"].search_count(
            [("session_id", "=", self.fil.id)]), "aucun message neuf")

    def test_un_ecran_qui_revient_prend_la_main_d_un_fil_mort(self):
        """Odoo a redémarré : le fil qui écrivait le tour est mort, son signe de
        vie vieillit. L'écran qui suit le tour doit le reprendre et le mener à
        l'enregistrement, pas attendre indéfiniment."""
        self.env["ir.config_parameter"].sudo().set_param("bf_ai_bridge.tenant", "bf")
        self.pending.sudo().write({"turn_payload": json.dumps(
            {"message": "Ma question", "tenant": "bf", "user_id": self.user.id})})
        appels = []
        cr = self.env.cr

        def pont(socket_path, endpoint, payload, timeout, headers=None):
            appels.append(endpoint)
            if len(appels) == 1:
                # Le suiveur : le tour du pont est fini, et le fil ne vit plus.
                cr.execute(
                    "UPDATE claude_chat_message SET runner_heartbeat = %s WHERE id = %s",
                    (fields.Datetime.now() - timedelta(minutes=5), self.pending.id))
                return iter([])
            return iter([trame("meta", {"session_id": "bf:neuf"}),
                         trame("text", {"delta": "Réponse reprise."}),
                         trame("done", {"response": "Réponse reprise."})])

        with patch.object(turns.transport, "stream", pont), \
                patch("odoo.addons.bf_claude_chat.controllers.main._WATCH_POLLS", 2):
            evenements = self.sse("/claude-chat/attach", {"turn_id": self.pending.id})
        self.assertEqual(appels, ["/chat-attach", "/chat-attach"])
        self.assertEqual(dict(evenements).get("final", {}).get("content"), "Réponse reprise.")
        self.pending.invalidate_recordset()
        self.assertEqual(self.pending.state, "done")

    def test_une_question_renvoyee_retrouve_son_tour(self):
        self.pending.write({"state": "done", "content": "Déjà répondu"})
        evenements = self.sse("/claude-chat/stream", {
            "session_id": self.fil.id, "message": "Ma question",
            "client_token": "jetonbanc0001"})
        final = dict(evenements)["final"]
        self.assertEqual(final["content"], "Déjà répondu")
        self.assertEqual(self.env["claude.chat.message"].search_count(
            [("session_id", "=", self.fil.id)]), 1)

    def test_revenir_a_un_tour_fini_rend_ce_qui_est_enregistre(self):
        self.pending.write({"state": "error", "content": "Partiel", "end_reason": "timeout"})
        evenements = self.sse("/claude-chat/attach", {"turn_id": self.pending.id})
        final = dict(evenements)["final"]
        self.assertEqual((final["content"], final["state"], final["end_reason"]),
                         ("Partiel", "error", "timeout"))

    def test_on_ne_se_rattache_pas_au_tour_d_un_autre(self):
        autre = self.env["claude.chat.session"].create({
            "name": "Autre", "user_id": self.env.ref("base.user_admin").id})
        message = self.env["claude.chat.message"].create({
            "session_id": autre.id, "role": "assistant", "content": "secret",
            "state": "done"})
        evenements = self.sse("/claude-chat/attach", {"turn_id": message.id})
        self.assertEqual(dict(evenements)["error"]["reason"], "not_found")
        self.assertNotIn("secret", json.dumps(evenements))

    def test_arreter_pose_le_drapeau_et_previent_le_pont(self):
        with patch.object(turns.transport, "post") as post:
            self.make_jsonrpc_request("/claude-chat/stop", {"turn_id": self.pending.id})
        self.pending.invalidate_recordset()
        self.assertTrue(self.pending.stop_requested)
        endpoint, charge = post.call_args[0][1], post.call_args[0][2]
        self.assertEqual(endpoint, "/chat-cancel")
        self.assertEqual(charge["turn_key"], "banc:2:" + "y" * 20)


@tagged("post_install", "-at_install")
class TestChampsDuTourReservesAuServeur(TransactionCase):
    """🔴 Relecture adverse : `readonly=True` ne garde que l'écran, et un employé
    peut écrire ses propres messages par RPC. Les champs qui pilotent le fil ne
    s'écrivent que côté serveur."""

    def setUp(self):
        super().setUp()
        self.usager = self.env["res.users"].create({
            "name": "Employé", "login": "employe_tours",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id])]})
        self.session = self.env["claude.chat.session"].with_user(self.usager).create(
            {"name": "Essai"})

    def test_un_employe_ne_fabrique_pas_un_tour_en_cours(self):
        Message = self.env["claude.chat.message"].with_user(self.usager)
        for champs in ({"state": "pending"}, {"turn_key": "a" * 30},
                       {"runner_heartbeat": fields.Datetime.now()},
                       {"stop_requested": True}, {"auto_continue_count": 3}):
            with self.assertRaises(AccessError, msg=str(champs)):
                Message.create(dict({"session_id": self.session.id, "role": "assistant",
                                     "content": "…"}, **champs))

    def test_ni_par_les_valeurs_par_defaut_du_contexte(self):
        """Les clés `default_*` des champs du tour sont ignorées : le message
        naît terminé, sans clé ni charge."""
        Message = self.env["claude.chat.message"].with_user(self.usager).with_context(
            default_state="pending", default_turn_key="d" * 30,
            default_turn_payload=json.dumps({"tenant": "demo"}))
        ligne = Message.create({"session_id": self.session.id, "role": "assistant",
                                "content": "…"}).sudo()
        self.assertEqual(ligne.state, "done")
        self.assertFalse(ligne.turn_key)
        self.assertFalse(ligne.turn_payload)

    def test_ni_par_une_valeur_par_defaut_personnelle(self):
        self.env["ir.default"].sudo().set(
            "claude.chat.message", "state", "pending", user_id=self.usager.id)
        with self.assertRaises(AccessError):
            self.env["claude.chat.message"].with_user(self.usager).create({
                "session_id": self.session.id, "role": "assistant", "content": "…"})

    def test_un_employe_ne_remet_pas_un_vieux_tour_en_cours(self):
        ligne = self.env["claude.chat.message"].with_user(self.usager).create({
            "session_id": self.session.id, "role": "assistant", "content": "fini"})
        with self.assertRaises(AccessError):
            ligne.write({"state": "pending"})
        with self.assertRaises(AccessError):
            ligne.write({"turn_key": "b" * 30})
        ligne.write({"content": "corrigé"})  # le reste reste permis
        self.assertEqual(ligne.content, "corrigé")

    def test_la_cle_du_tour_ne_se_lit_pas_hors_administration(self):
        ligne = self.env["claude.chat.message"].sudo().create({
            "session_id": self.session.id, "role": "assistant", "content": "…",
            "state": "pending", "turn_key": "c" * 30})
        with self.assertRaises(AccessError):
            ligne.with_user(self.usager).read(["turn_key"])

