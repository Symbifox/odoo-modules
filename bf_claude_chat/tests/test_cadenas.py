"""Le cadenas d'un tour de Gen : ce qui doit rester vrai sans vrai pont.

Le pont est remplacé par des flux écrits à la main, comme dans `test_tours` : chaque essai
dit ce que le pont rend, puis lit ce que le fil a ENREGISTRÉ — c'est l'enregistrement qui
doit faire foi, pas l'affichage, puisque le téléphone et une conversation relue trois jours
plus tard ne lisent que lui.

Trois exigences, dans cet ordre :
1. le plus FAIBLE des départs l'emporte (un tour d'Odoo vaut plusieurs tours du pont) ;
2. un cadenas que personne n'a MESURÉ se présente comme annoncé, jamais comme constaté ;
3. personne d'autre que le fil, en superutilisateur, ne peut écrire ce champ.
"""

import json
from contextlib import ExitStack
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests.common import HttpCase, TransactionCase, new_test_user, tagged

from ..controllers import turns
from .test_tours import Enregistreur, FauxPont, trame


@tagged("post_install", "-at_install")
class TestRegleDuCadenas(TransactionCase):
    """La règle pure : `cadenas_retenu`. Aucun pont, aucune base."""

    def retenu(self, *departs):
        courant = ("", False)
        for etat, constate in departs:
            courant = turns.cadenas_retenu(courant, etat, constate)
        return courant

    def test_un_seul_depart(self):
        self.assertEqual(self.retenu(("complet", True)), ("complet", True))

    def test_la_mesure_confirme_lannonce(self):
        self.assertEqual(self.retenu(("complet", False), ("complet", True)),
                         ("complet", True))

    def test_la_mesure_degrade_lannonce_et_garde_son_constate(self):
        """Le cas du tour exclu : la sonde annonçait « complet », le relais a compté
        des noms partis en clair."""
        self.assertEqual(self.retenu(("complet", False), ("identifiants", True)),
                         ("identifiants", True))

    def test_le_plus_faible_lemporte_quel_que_soit_lordre(self):
        for depart in (("complet", True), ("clair", True)):
            self.assertEqual(self.retenu(depart, ("clair", True))[0], "clair")
        self.assertEqual(self.retenu(("clair", True), ("complet", True)),
                         ("clair", True), "un « complet » plus tard n'efface pas le clair")

    def test_un_etat_inconnu_ne_touche_a_rien(self):
        for inconnu in ("", None, False, "inconnu", "presque"):
            self.assertEqual(self.retenu(("complet", True), (inconnu, True)),
                             ("complet", True))

    def test_rien_du_tout_reste_rien(self):
        self.assertEqual(self.retenu((None, False)), ("", False))


@tagged("post_install", "-at_install")
class TestCadenasDuTour(TransactionCase):
    """Ce que le fil enregistre, sur un vrai tour, flux du pont simulé."""

    def setUp(self):
        super().setUp()
        self.registry.enter_test_mode(self.cr)
        self.addCleanup(self.registry.leave_test_mode)
        ICP = self.env["ir.config_parameter"].sudo()
        ICP.set_param("bf_ai_bridge.tenant", "bf")
        ICP.set_param("bf_claude_chat.tenant", "bf")
        self.session = self.env["claude.chat.session"].create({
            "name": "New Chat", "user_id": self.env.uid})
        self.env["claude.chat.message"].create({
            "session_id": self.session.id, "role": "user", "content": "Ma question"})
        self.message = self.env["claude.chat.message"].create({
            "session_id": self.session.id, "role": "assistant", "content": "…",
            "state": "pending"})
        self.message.sudo().write({
            "turn_key": "banc:1:" + "x" * 20,
            "turn_payload": json.dumps({"message": "Ma question", "tenant": "bf",
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

    @staticmethod
    def final(ecoute):
        brut = b"".join(ecoute.recu)
        for _m, evs in turns.iter_events([brut]):
            for nom, data in evs:
                if nom == "final":
                    return data
        return {}

    def test_annonce_puis_mesure(self):
        ecoute = self.lancer(FauxPont([
            trame("masque", {"etat": "complet", "constate": False}),
            trame("meta", {"session_id": "bf:neuf"}),
            trame("text", {"delta": "Voici."}),
            trame("done", {"session_id": "bf:neuf", "response": "Voici."}),
            trame("masque", {"etat": "complet", "constate": True}),
        ]))
        self.assertEqual(self.message.masque, "complet")
        self.assertTrue(self.message.masque_constate)
        self.assertEqual(self.final(ecoute)["masque"], "complet")
        self.assertIs(self.final(ecoute)["masque_constate"], True)
        # L'écran voit le cadenas AVANT la réponse : c'est là qu'il sert.
        evenements = ecoute.evenements()
        self.assertLess(evenements.index("masque"), evenements.index("done"))

    def test_la_mesure_degrade_ce_qui_etait_annonce(self):
        self.lancer(FauxPont([
            trame("masque", {"etat": "complet", "constate": False}),
            trame("done", {"response": "Voici."}),
            trame("masque", {"etat": "identifiants", "constate": True}),
        ]))
        self.assertEqual((self.message.masque, self.message.masque_constate),
                         ("identifiants", True))

    def test_sans_mesure_le_cadenas_reste_annonce(self):
        """Le relais s'est tu à la fin du tour : on ne prétend pas avoir constaté."""
        self.lancer(FauxPont([
            trame("masque", {"etat": "complet", "constate": False}),
            trame("done", {"response": "Voici."}),
        ]))
        self.assertEqual((self.message.masque, self.message.masque_constate),
                         ("complet", False))

    def test_une_reprise_moins_masquee_degrade_le_tour(self):
        """🔴 Un tour d'Odoo vaut plusieurs tours du pont : le relais peut tomber
        entre les deux, et le second départ part alors en clair."""
        ecoute = self.lancer(FauxPont(
            [trame("masque", {"etat": "complet", "constate": True}),
             trame("text", {"delta": "Partie un."}),
             trame("error", {"reason": "timeout", "response": "Partie un.",
                             "interrupted": True})],
            [trame("masque", {"etat": "clair", "constate": True}),
             trame("done", {"response": "Partie deux."})],
        ))
        self.assertIn("resume", ecoute.evenements())
        self.assertEqual(self.message.auto_continue_count, 1)
        self.assertEqual((self.message.masque, self.message.masque_constate),
                         ("clair", True))

    def test_une_reprise_mieux_masquee_neffance_pas_le_clair(self):
        self.lancer(FauxPont(
            [trame("masque", {"etat": "clair", "constate": True}),
             trame("error", {"reason": "timeout", "response": "Partie un.",
                             "interrupted": True})],
            [trame("masque", {"etat": "complet", "constate": True}),
             trame("done", {"response": "Partie deux."})],
        ))
        self.assertEqual(self.message.masque, "clair")

    def test_un_tour_repris_garde_le_cadenas_du_premier_depart(self):
        """🔴 `_load` restituait le texte déjà écrit, les
        outils et le compte de reprises, mais PAS le cadenas. Un tour repris par un autre
        fil — travailleur recyclé, Odoo redémarré, cron qui ramasse un tour orphelin —
        repartait donc à vide, et la fin du tour écrivait ce vide par-dessus ce que le
        premier fil avait constaté."""
        self.message.sudo().write({"masque": "clair", "masque_constate": True})
        self.env.flush_all()
        self.lancer(FauxPont([
            trame("masque", {"etat": "complet", "constate": True}),
            trame("done", {"response": "Suite."}),
        ]), attach=True)
        self.assertEqual((self.message.masque, self.message.masque_constate),
                         ("clair", True), "le départ d'avant ce fil compte encore")

    def test_lecran_ne_voit_jamais_un_cadenas_plus_fort_que_la_base(self):
        """🔴 L'écran applique la DERNIÈRE trame reçue. Un départ mesuré « clair », puis
        une reprise qui annonce « complet », lui faisait afficher « Masqué » pendant toute
        la seconde moitié de la réponse, alors que la base disait déjà « Rien de masqué »
. La trame relayée porte donc l'état
        RETENU, pas celle du pont."""
        ecoute = self.lancer(FauxPont(
            [trame("masque", {"etat": "clair", "constate": True}),
             trame("error", {"reason": "timeout", "response": "Un.", "interrupted": True})],
            [trame("masque", {"etat": "complet", "constate": True}),
             trame("done", {"response": "Deux."})],
        ))
        relayes = []
        for _m, evs in turns.iter_events([b"".join(ecoute.recu)]):
            for nom, data in evs:
                if nom == "masque":
                    relayes.append(data)
        self.assertEqual(relayes, [{"etat": "clair", "constate": True},
                                   {"etat": "clair", "constate": True}],
                         "le second départ ne remonte pas le cadenas à l'écran")
        self.assertEqual(self.message.masque, "clair")

    def test_un_pont_sans_cadenas_ne_laisse_rien(self):
        """Un locataire hors du relais, ou un pont d'avant le cadenas : AUCUN cadenas.
        En afficher un dirait qu'un masque a manqué à son devoir."""
        ecoute = self.lancer(FauxPont([trame("done", {"response": "Voici."})]))
        self.assertFalse(self.message.masque)
        self.assertFalse(self.message.masque_constate)
        self.assertNotIn("masque", ecoute.evenements())
        self.assertEqual(self.final(ecoute)["masque"], "")

    def test_le_cadenas_est_ecrit_avant_la_fin_du_tour(self):
        """Le téléphone n'a pas de flux : il sonde. Et un fil qui meurt doit laisser
        derrière lui ce qu'il savait du masque."""
        ecrits = []
        vraie_ecriture = turns.TurnProgress.write

        def espion(self, force=False, **extra):
            if "masque" in extra:
                ecrits.append((extra["masque"], extra.get("masque_constate"), force))
            return vraie_ecriture(self, force=force, **extra)

        with patch.object(turns.TurnProgress, "write", espion):
            self.lancer(FauxPont([
                trame("masque", {"etat": "identifiants", "constate": False}),
                trame("text", {"delta": "a"}),
                trame("done", {"response": "a"}),
            ]))
        self.assertIn(("identifiants", False, True), ecrits,
                      "le cadenas annoncé est écrit tout de suite, sans attendre")
        self.assertEqual(self.message.masque, "identifiants")

    def test_la_fin_du_tour_rattrape_un_avancement_qui_na_rien_ecrit(self):
        """🔴 `TurnProgress.write` avale ses erreurs, par choix : « l'avancement est un
        confort, son échec ne doit pas casser le tour ». Le cadenas ne doit pas disparaître
        avec lui — l'enregistrement final, lui, est protégé par `retrying`."""
        with patch.object(turns.TurnProgress, "write", lambda self, force=False, **kw: None):
            self.lancer(FauxPont([
                trame("masque", {"etat": "clair", "constate": True}),
                trame("done", {"response": "Voici."}),
            ]))
        self.assertEqual(self.message.content, "Voici.", "le tour s'enregistre quand même")
        self.assertEqual((self.message.masque, self.message.masque_constate),
                         ("clair", True), "le cadenas est écrit par la fin du tour")

    def test_un_etat_inventé_par_le_pont_est_ignore(self):
        self.lancer(FauxPont([
            trame("masque", {"etat": "presque", "constate": True}),
            trame("done", {"response": "Voici."}),
        ]))
        self.assertFalse(self.message.masque)


@tagged("post_install", "-at_install")
class TestCadenasNonForgeable(TransactionCase):
    """🔴 Un cadenas que l'usager peut écrire lui-même ne prouve rien."""

    def setUp(self):
        super().setUp()
        # ⚠️ Un VRAI usager : `self.env` des essais est en superutilisateur, où la garde
        # laisse tout passer. L'essai qui l'oublie ne mesure rien.
        self.user = new_test_user(self.env, login="banc_cadenas_rpc")
        self.session = self.env["claude.chat.session"].sudo().create({
            "name": "Essai", "user_id": self.user.id})

    def message(self, **vals):
        return self.env["claude.chat.message"].sudo().create(dict({
            "session_id": self.session.id, "role": "assistant", "content": "x",
        }, **vals)).with_user(self.user)

    def test_un_usager_ne_peut_pas_ecrire_le_cadenas(self):
        message = self.message()
        for vals in ({"masque": "complet"}, {"masque_constate": True},
                     {"masque": "complet", "masque_constate": True}):
            with self.assertRaises(AccessError):
                message.write(vals)
        # Le reste de la bulle s'écrit toujours : la garde ne ferme que ces champs.
        message.write({"content": "y"})

    def test_un_usager_ne_peut_pas_le_poser_a_la_creation(self):
        with self.assertRaises(AccessError):
            self.env["claude.chat.message"].with_user(self.user).create({
                "session_id": self.session.id, "role": "assistant", "content": "x",
                "masque": "complet", "masque_constate": True})

    def test_le_serveur_le_peut(self):
        self.assertEqual(self.message(masque="clair").masque, "clair")

    def test_un_ir_default_personnel_ne_forge_pas_le_cadenas(self):
        """🔴 La route que la première version laissait ouverte. Les valeurs par défaut s'ajoutent APRÈS le contrôle de
        `create`, et le filtre du CONTEXTE ne voit pas un `ir.default` personnel — que
        tout employé peut poser, `ir.default` étant en 1,1,1,1 pour `base.group_user`.
        Sans la seconde ceinture, l'usager se fabriquait une réponse « Masqué · constaté ».
        """
        Defaut = self.env["ir.default"].with_user(self.user)
        for champ, valeur in (("masque", "complet"), ("masque_constate", True)):
            Defaut.set("claude.chat.message", champ, valeur, user_id=self.user.id)
        with self.assertRaises(AccessError):
            self.env["claude.chat.message"].with_user(self.user).create({
                "session_id": self.session.id, "role": "assistant", "content": "forgé"})

    def test_le_champ_est_dans_la_garde(self):
        self.assertLessEqual({"masque", "masque_constate"},
                             self.env["claude.chat.message"].TURN_FIELDS)


@tagged("post_install", "-at_install")
class TestCadenasSuiveur(HttpCase):
    """L'écran qui SUIT le tour de quelqu'un d'autre (`/claude-chat/attach`).

    🔴 Un mutant survivait : retirer le `cadenas_vu` du suiveur ne faisait
    échouer aucun essai. Celui-ci passe par la vraie route : un tour dont la base dit déjà
    « clair », un pont qui rejoue une trame « complet », et le suiveur ne doit pas remonter
    le cadenas à l'écran.
    """

    def setUp(self):
        super().setUp()
        self.user = new_test_user(self.env, login="banc_cadenas_suiveur",
                                  password="banc_cadenas_suiveur")
        self.env["ir.config_parameter"].sudo().set_param("bf_claude_chat.enabled", "True")
        # ⚠️ `self.conv`, jamais `self.session` : ce nom-là est celui de la session HTTP
        # de `HttpCase`, et `authenticate()` meurt dessus.
        self.conv = self.env["claude.chat.session"].sudo().create({
            "name": "Suivi", "user_id": self.user.id})
        self.message = self.env["claude.chat.message"].sudo().create({
            "session_id": self.conv.id, "role": "assistant", "content": "…",
            "state": "pending"})
        self.message.sudo().write({
            "turn_key": "banc:suivi:" + "y" * 20,
            "runner_heartbeat": fields.Datetime.now(),
            "masque": "clair", "masque_constate": True,
        })
        # ⚠️ Pas de `commit` : il est interdit dans un essai, et `HttpCase` met déjà le
        # registre en mode d'essai — les curseurs que `_follow` ouvre à part partagent
        # donc la transaction, et voient ces écritures.
        self.env.flush_all()

    def test_le_suiveur_ne_remonte_pas_le_cadenas(self):
        from ..controllers import main as controleur

        trames = [trame("masque", {"etat": "complet", "constate": True}),
                  trame("text", {"delta": "suite"})]
        with ExitStack() as pile:
            pile.enter_context(patch.object(turns, "claim", lambda *a, **k: False))
            pile.enter_context(patch.object(turns.transport, "stream",
                                            lambda *a, **k: iter(trames)))
            pile.enter_context(patch.object(controleur, "_WATCH_ROUNDS", 1))
            pile.enter_context(patch.object(controleur, "_WATCH_POLLS", 0))
            self.authenticate("banc_cadenas_suiveur", "banc_cadenas_suiveur")
            # ⚠️ Lu AU FIL DE L'EAU et arrêté à la trame cherchée : la réponse est un flux
            # découpé, et la lire jusqu'au bout lève `InvalidChunkLength` quand le
            # générateur s'arrête (la fin du découpage n'est pas écrite).
            reponse = self.opener.post(
                self.base_url() + "/claude-chat/attach",
                data=json.dumps({"turn_id": self.message.id}),
                headers={"Content-Type": "application/json", "X-Claude-Stream": "1"},
                stream=True, timeout=60)
            self.assertEqual(reponse.status_code, 200)
            lues = []
            try:
                for ligne in reponse.iter_lines():
                    lues.append(ligne or b"")
                    if lues[-2:-1] == [b"event: masque"]:
                        break
            except Exception:  # noqa: BLE001
                # Le générateur s'arrête sans écrire la fin du découpage : ce qui est
                # arrivé avant est bon, et c'est tout ce que cet essai regarde.
                pass
            finally:
                reponse.close()
            vus = [d for _m, evs in turns.iter_events([b"\n".join(lues) + b"\n\n"])
                   for nom, d in evs if nom == "masque"]
        self.assertEqual(vus, [{"etat": "clair", "constate": True}],
                         "le suiveur relaie l'état RETENU, pas celui du pont ; "
                         "reçu : %s" % (b" | ".join(lues)[:600],))


@tagged("post_install", "-at_install")
class TestCadenasMobile(HttpCase):
    """Le téléphone lit le même cadenas que le bureau (api 11)."""

    def setUp(self):
        super().setUp()
        Device = next((self.env[m] for m in ("bf.email.mobile.device",
                                             "sms.archive.mobile.device")
                       if m in self.env), None)
        if Device is None:
            self.skipTest("aucun modèle d'appareil mobile sur cette base")
        self.user = self.env["res.users"].create({
            "name": "Banc cadenas", "login": "banc_cadenas",
            "groups_id": [(6, 0, self.env.ref("base.group_user").ids)]})
        self.jeton = Device._issue(self.user.id, name="Banc cadenas").device_token
        self.env["ir.config_parameter"].sudo().set_param("bf_claude_chat.enabled", "True")
        self.session = self.env["claude.chat.session"].create({
            "name": "Essai", "user_id": self.user.id})
        self.message = self.env["claude.chat.message"].sudo().create({
            "session_id": self.session.id, "role": "assistant", "content": "Voici.",
            "masque": "identifiants", "masque_constate": True})
        self.nu = self.env["claude.chat.message"].sudo().create({
            "session_id": self.session.id, "role": "assistant", "content": "Avant."})

    def _appel(self, route):
        return self.url_open("/bf_claude_chat/mobile/v1" + route,
                             headers={"Authorization": "Bearer %s" % self.jeton},
                             timeout=30)

    def test_la_surface_annonce_son_niveau(self):
        self.assertGreaterEqual(self._appel("/ping").json()["api"], 11)

    def test_les_messages_portent_le_cadenas(self):
        rangs = {r["id"]: r for r in
                 self._appel("/messages?session_id=%d" % self.session.id).json()["messages"]}
        self.assertEqual(rangs[self.message.id]["masque"], "identifiants")
        self.assertIs(rangs[self.message.id]["masque_constate"], True)
        # ⚠️ Jamais `false` pour un message d'avant le cadenas : l'app lit une chaîne.
        self.assertEqual(rangs[self.nu.id]["masque"], "")
        self.assertIs(rangs[self.nu.id]["masque_constate"], False)

    def test_le_tour_porte_le_cadenas(self):
        tour = self._appel("/turn?turn_id=%d" % self.message.id).json()
        self.assertEqual(tour["masque"], "identifiants")
        self.assertIs(tour["masque_constate"], True)
        self.assertEqual(self._appel("/turn?turn_id=%d" % self.nu.id).json()["masque"], "")
