"""Chaque conversation Gen vise sa fermeture, comme un billet.

Trois choses doivent rester vraies :

- la balise de fin de tour n'atteint JAMAIS un écran ni la base, quel que soit
  le découpage du flux, et la question enregistrée reste celle de la personne ;
- le jugement de Gen se lit sur la conversation, et la proposition de tâche ne
  trahit rien de ce que la personne ne peut pas lire ;
- la passe de nuit relance une fois par période d'inactivité, sans bruit, et
  n'archive jamais rien.
"""

import json
import random
import re
from contextlib import ExitStack
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests.common import HttpCase, TransactionCase, new_test_user, tagged

from ..closure import ClosureFilter, closure_note, strip_closure
from ..controllers import turns
from ..models import claude_chat_session as module_session
from .test_tours import Enregistreur, FauxPont, trame

# La devinette de langue du pont (`bridge/server.py`, `_detect_lang`) : la
# consigne ne doit pas faire passer ses messages de repli à l'anglais.
_ANGLAIS_DU_PONT = re.compile(r"\b(the|please|can you|could you|what|why|how|when|where)\b")


@tagged("post_install", "-at_install")
class TestFiltre(TransactionCase):

    def _couper(self, texte, graine):
        hasard = random.Random(graine)
        filtre, sortie, i = ClosureFilter(), [], 0
        while i < len(texte):
            n = hasard.randint(1, 7)
            sortie.append(filtre.feed(texte[i:i + n]))
            i += n
        sortie.append(filtre.finish())
        return "".join(sortie), filtre.verdict

    def test_la_balise_ne_sort_jamais_quel_que_soit_le_decoupage(self):
        texte = ("C'est en production et vérifié.\n\n"
                 '<closure state="done">module posé et vérifié</closure>\n')
        for graine in range(300):
            sortie, verdict = self._couper(texte, graine)
            self.assertNotIn("<", sortie, graine)
            self.assertEqual(sortie.strip(), "C'est en production et vérifié.")
            self.assertEqual(verdict["state"], "done")
            self.assertEqual(verdict["reason"], "module posé et vérifié")

    def test_une_mention_dans_du_code_reste_et_ne_retient_rien(self):
        filtre = ClosureFilter()
        self.assertEqual(filtre.feed("voir `<closure` ici, puis la suite"),
                         "voir `<closure` ici, puis la suite")
        self.assertIsNone(filtre.verdict)

    def test_une_balise_coupee_par_la_fin_ne_s_affiche_pas(self):
        propre, verdict = strip_closure('Fin.\n<closure state="open">raison sans fin')
        self.assertEqual(propre.strip(), "Fin.")
        self.assertIsNone(verdict)

    def test_une_balise_sur_plusieurs_lignes_ou_coupee_tot(self):
        texte = 'Fait.\n<closure\n state="done">livré\n</closure>'
        for graine in range(100):
            sortie, verdict = self._couper(texte, graine)
            self.assertEqual(sortie.strip(), "Fait.", graine)
            self.assertEqual(verdict["state"], "done")
        self.assertEqual(strip_closure("Fin. <closure st")[0].strip(), "Fin.")

    def test_le_suiveur_filtre_comme_le_fil(self):
        """L'écran qui revient sur un tour en cours passe par `_watch`, qui
        relaie le pont lui-même : même filtre, même nettoyage de `done`."""
        brut = (trame("text", {"delta": "Voilà.<clo"})
                + trame("text", {"delta": 'sure state="done">fait</closure>'})
                + trame("done", {"response": 'Voilà.<closure state="done">fait</closure>'}))
        filtre = ClosureFilter()
        sortie = b"".join(
            b for trame_, evs in turns.relay_frames([brut[:17], brut[17:60], brut[60:]])
            for b in turns.cleaned_frames(trame_, evs, filtre.feed)).decode()
        self.assertNotIn("closure", sortie)
        self.assertIn('"response": "Voilà."', sortie)

    def test_un_chevron_ordinaire_passe(self):
        self.assertEqual(strip_closure("a < b et <clo")[0], "a < b et <clo")

    def test_etat_inconnu_et_tache(self):
        self.assertIsNone(strip_closure('<closure state="fini">x</closure>')[1])
        _p, verdict = strip_closure('x <closure state="waiting" task="1234">feu vert</closure>')
        self.assertEqual((verdict["state"], verdict["task_id"]), ("waiting", 1234))
        _p, verdict = strip_closure('<closure state="open" task="abc">x</closure>')
        self.assertEqual(verdict["task_id"], 0)

    def test_la_consigne_ne_change_pas_la_langue_du_pont(self):
        for note in (closure_note(), closure_note(unlinked=True)):
            self.assertFalse(_ANGLAIS_DU_PONT.search(note.lower()))
        self.assertNotIn("task=", closure_note())
        self.assertIn("task=", closure_note(unlinked=True))


class _Base(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(cls.env, login="banc_fermeture",
                                 groups="base.group_user,project.group_project_user")
        ouvert = cls.env["project.project"].create({
            "name": "Projet ouvert", "privacy_visibility": "employees"})
        ferme = cls.env["project.project"].create({
            "name": "Projet fermé", "privacy_visibility": "followers"})
        cls.tache = cls.env["project.task"].create(
            {"name": "Tâche visible", "project_id": ouvert.id})
        cls.cachee = cls.env["project.task"].create(
            {"name": "Tâche cachée", "project_id": ferme.id})

    def setUp(self):
        super().setUp()
        ICP = self.env["ir.config_parameter"].sudo()
        ICP.set_param("bf_ai_bridge.tenant", "bf")
        ICP.set_param("bf_claude_chat.tenant", "bf")
        ICP.set_param("bf_claude_chat.closure_enabled", "True")


@tagged("post_install", "-at_install")
class TestTourAvecFermeture(_Base):

    def setUp(self):
        super().setUp()
        self.registry.enter_test_mode(self.cr)
        self.addCleanup(self.registry.leave_test_mode)

    def _tour(self, flux, question="Ma question", **session_vals):
        session = self.env["claude.chat.session"].create(dict({
            "name": "New Chat", "user_id": self.user.id,
            "claude_session_id": "bf:ancien"}, **session_vals))
        self.env["claude.chat.message"].create({
            "session_id": session.id, "role": "user", "content": question})
        message = self.env["claude.chat.message"].create({
            "session_id": session.id, "role": "assistant", "content": "…",
            "state": "pending"})
        message.sudo().write({
            "turn_key": "banc:1:" + "y" * 20,
            "turn_payload": json.dumps({"message": question, "tenant": "bf",
                                        "session_id": "bf:ancien",
                                        "user_id": self.user.id}),
            "runner_heartbeat": fields.Datetime.now(),
        })
        self.env.flush_all()
        pont = FauxPont(flux) if not isinstance(flux, tuple) else FauxPont(*flux)
        ecoute = Enregistreur()
        with ExitStack() as pile:
            pile.enter_context(patch.object(turns.transport, "stream", pont))
            pile.enter_context(patch.object(turns, "_wait_for_bridge", lambda *a, **k: True))
            turns.run_turn(self.cr.dbname, message.id, "/nulle/part.sock", 5,
                           listener=ecoute)
        self.env.invalidate_all()
        return session, message, pont, ecoute

    @staticmethod
    def _relaye(ecoute):
        return b"".join(ecoute.recu).decode()

    def test_la_consigne_part_au_pont_et_la_balise_ne_revient_nulle_part(self):
        reponse = ("C'est livré.\n\n"
                   '<closure state="done">livré et vérifié</closure>')
        session, message, pont, ecoute = self._tour([
            trame("meta", {"session_id": "bf:neuf"}),
            trame("text", {"delta": "C'est livré.\n\n<clo"}),
            trame("text", {"delta": 'sure state="done">livré et vé'}),
            trame("text", {"delta": "rifié</closure>"}),
            trame("done", {"session_id": "bf:neuf", "response": reponse}),
        ])
        charge = pont.appels[0][1]
        self.assertTrue(charge["message"].startswith("Ma question\n\n<fermeture>"))
        self.assertIn('<closure state="ÉTAT">', charge["message"])
        # La personne, sa question, l'écran et la base ne voient rien de tout ça.
        question = session.message_ids.filtered(lambda m: m.role == "user")
        self.assertEqual(question.content, "Ma question")
        self.assertEqual(session.name, "Ma question")
        self.assertEqual(message.content, "C'est livré.")
        relaye = self._relaye(ecoute)
        self.assertNotIn("<closure", relaye)
        self.assertNotIn("<fermeture", relaye)
        self.assertNotIn("livré et vé", relaye.split("event: final")[0])
        self.assertIn("C'est livré.", relaye)
        # Le jugement est sur la conversation, et dans l'événement final.
        self.assertEqual(session.closure_state, "done")
        self.assertEqual(session.closure_reason, "livré et vérifié")
        self.assertTrue(session.last_activity)
        evs =[ev for _m, lot in turns.iter_events([b"".join(ecoute.recu)]) for ev in lot]
        fin = [d for e, d in evs if e == "final"][0]
        self.assertEqual(fin["closure"]["closure_state"], "done")
        fait = [d for e, d in evs if e == "done"][0]
        self.assertEqual(fait["response"], "C'est livré.")

    def test_une_reprise_garde_la_consigne(self):
        session, message, pont, _e = self._tour((
            [trame("meta", {"session_id": "bf:neuf"}),
             trame("text", {"delta": "Partie un."}),
             trame("error", {"reason": "timeout", "response": "Partie un.",
                             "interrupted": True})],
            [trame("meta", {"session_id": "bf:neuf"}),
             trame("text", {"delta": 'Partie deux.<closure state="waiting">ton feu vert</closure>'}),
             trame("done", {"response": 'Partie deux.<closure state="waiting">ton feu vert</closure>'})],
        ))
        self.assertIn("<fermeture>", pont.appels[1][1]["message"])
        self.assertEqual(message.content, "Partie un.\n\nPartie deux.")
        self.assertEqual(session.closure_state, "waiting")

    def test_reglage_eteint_rien_ne_change(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_claude_chat.closure_enabled", "False")
        session, message, pont, _e = self._tour([
            trame("text", {"delta": "Réponse."}),
            trame("done", {"response": "Réponse."}),
        ])
        self.assertEqual(pont.appels[0][1]["message"], "Ma question")
        self.assertFalse(session.closure_state)
        self.assertTrue(session.last_activity)

    def test_une_passe_sans_personne_n_a_pas_de_consigne(self):
        session, _m, pont, _e = self._tour([
            trame("done", {"response": "ok"})], origin="refine_meeting")
        self.assertEqual(pont.appels[0][1]["message"], "Ma question")
        self.assertFalse(session.closure_state)

    def test_un_tour_sans_balise_ou_en_erreur_n_est_pas_juge(self):
        session, _m, _p, _e = self._tour([
            trame("done", {"response": "J'ai oublié la balise."})],
            closure_state="done", closure_reason="ancien")
        self.assertFalse(session.closure_state)
        self.assertFalse(session.closure_reason)
        # Un tour en erreur ou arrêté laisse du travail : ouverte, jamais
        # « dort » deux jours plus tard.
        session, _m, _p, _e = self._tour([
            trame("text", {"delta": '<closure state="done">x</closure>'}),
            trame("error", {"reason": "stopped", "response": ""})])
        self.assertEqual(session.closure_state, "open")

    def test_la_tache_proposee_une_fois_et_seulement_lisible(self):
        balise = f'<closure state="open" task="{self.tache.id}">suite</closure>'
        session, _m, pont, _e = self._tour([trame("done", {"response": "ok " + balise})])
        self.assertIn("task=", pont.appels[0][1]["message"])
        self.assertEqual(session.link_task_id, self.tache)
        self.assertTrue(session.link_proposed)
        # Une tâche que la personne ne peut pas lire n'est pas proposée.
        balise = f'<closure state="open" task="{self.cachee.id}">suite</closure>'
        session, _m, _p, _e = self._tour([trame("done", {"response": balise})])
        self.assertFalse(session.link_task_id)
        self.assertFalse(session.link_proposed)
        # Déjà rattachée : ni consigne de tâche, ni proposition.
        balise = f'<closure state="open" task="{self.tache.id}">suite</closure>'
        session, _m, pont, _e = self._tour(
            [trame("done", {"response": balise})],
            res_model="project.task", res_id=self.tache.id)
        self.assertNotIn("task=", pont.appels[0][1]["message"])
        self.assertFalse(session.link_task_id)


@tagged("post_install", "-at_install")
class TestReponses(_Base):

    def _session(self, **vals):
        return self.env["claude.chat.session"].sudo().create(
            dict({"name": "Conv", "user_id": self.user.id}, **vals)).with_user(self.user)

    def test_archiver_ou_pas_encore(self):
        session = self._session(closure_state="done", closure_reason="fait")
        self.assertTrue(session._closure_answer("later"))
        self.assertEqual(session.closure_state, "open")
        self.assertTrue(session._closure_answer("archive"))
        self.assertFalse(session.active)
        self.assertFalse(session._closure_answer("n'importe"))

    def test_rattacher_la_tache_proposee(self):
        session = self._session(link_task_id=self.tache.id, link_proposed=True)
        self.assertTrue(session._link_answer(True))
        self.assertEqual((session.res_model, session.res_id), ("project.task", self.tache.id))
        self.assertFalse(session.link_task_id)
        session = self._session(link_task_id=self.tache.id, link_proposed=True)
        self.assertFalse(session._link_answer(False))
        self.assertFalse(session.res_model)

    def test_les_champs_de_fermeture_sont_au_serveur(self):
        session = self._session()
        for vals in ({"closure_state": "done"}, {"link_task_id": self.cachee.id},
                     {"last_activity": fields.Datetime.now()}, {"followup_date": False}):
            with self.assertRaises(AccessError):
                session.write(vals)
        with self.assertRaises(AccessError):
            self.env["claude.chat.session"].with_user(self.user).create(
                {"name": "x", "closure_state": "waiting"})

    def test_une_conversation_ne_change_pas_de_main(self):
        autre = new_test_user(self.env, login="banc_autre_main")
        session = self._session()
        with self.assertRaises(AccessError):
            session.write({"user_id": autre.id})
        with self.assertRaises(AccessError):
            self.env["claude.chat.session"].with_user(self.user).create(
                {"name": "x", "user_id": autre.id})
        session.write({"name": "renommée", "res_model": "project.task",
                       "res_id": self.tache.id})  # le reste s'écrit toujours

    def test_une_tache_forgee_ne_trahit_ni_son_nom_ni_son_rattachement(self):
        # Défense en profondeur : même posée par le serveur, une tâche que la
        # personne ne lit pas ne livre ni son nom ni son rattachement.
        session = self._session(link_task_id=self.cachee.id)
        self.assertFalse(session._closure_payload()["link_task"])
        self.assertFalse(session._link_answer(True))
        self.assertFalse(session.res_model)

    def test_a_suivre(self):
        Session = self.env["claude.chat.session"].with_user(self.user)
        a = self._session(closure_state="done")
        b = self._session(closure_state="waiting")
        c = self._session(closure_state="open")
        d = self._session(closure_state="open", followup_date=fields.Datetime.now())
        e = self._session(closure_state="ideation")
        f = self._session(closure_state="idle")
        trouvees = Session.search([("id", "in", (a | b | c | d | e | f).ids)]
                                  + Session._to_follow_domain())
        self.assertEqual(trouvees, a | b | d | f)


@tagged("post_install", "-at_install")
class TestPasseDeNuit(_Base):

    def _session(self, jours, **vals):
        session = self.env["claude.chat.session"].create(
            dict({"name": "Conv nuit", "user_id": self.user.id}, **vals))
        self.env["claude.chat.message"].create({
            "session_id": session.id, "role": "user", "content": "q"})
        quand = fields.Datetime.now() - timedelta(days=jours)
        session.write({"last_activity": quand, "list_date": quand})
        return session

    def _relances(self, session):
        return session.message_ids.filtered("followup")

    def test_relance_une_fois_par_periode_et_note_silencieuse(self):
        attend = self._session(3, closure_state="waiting", closure_reason="valider le courriel",
                               res_model="project.task", res_id=self.tache.id)
        ouverte = self._session(3, closure_state="open")
        notes_avant = self.tache.message_ids
        self.env["claude.chat.session"]._cron_closure_followup()
        relance = self._relances(attend)
        self.assertEqual(len(relance), 1)
        self.assertEqual(relance.role, "assistant")
        self.assertFalse(relance.internal)
        self.assertIn("valider le courriel", relance.content)
        self.assertTrue(self._relances(ouverte))
        note = self.tache.message_ids - notes_avant
        self.assertEqual(len(note), 1)
        self.assertEqual(note.subtype_id, self.env.ref("mail.mt_note"))
        # Signée OdooBot, jamais au nom de la personne (ni XP, ni usurpation).
        self.assertEqual(note.author_id, self.env.ref("base.partner_root"))
        self.assertIn(self.user.name, note.body)
        self.assertFalse(note.notification_ids)
        # Rien ne bouge : pas de seconde relance.
        self.env["claude.chat.session"]._cron_closure_followup()
        self.assertEqual(len(self._relances(attend)), 1)
        # On reparle, puis le silence revient : une nouvelle relance.
        attend.write(attend._closure_vals({"state": "waiting", "reason": "toujours"}))
        attend.last_activity = fields.Datetime.now() - timedelta(days=3)
        self.env["claude.chat.session"]._cron_closure_followup()
        self.assertEqual(len(self._relances(attend)), 2)

    def test_pas_de_note_sur_une_fiche_en_lecture_seule(self):
        projet = self.tache.project_id
        self.assertTrue(projet.with_user(self.user).has_access("read"))
        self.assertFalse(projet.with_user(self.user).has_access("write"))
        avant = projet.message_ids
        attend = self._session(3, closure_state="waiting",
                               res_model="project.project", res_id=projet.id)
        self.env["claude.chat.session"]._cron_closure_followup()
        self.assertTrue(self._relances(attend))
        self.assertEqual(projet.message_ids, avant)

    def test_les_relancees_sans_reponse_n_affament_pas_la_passe(self):
        deja = self._session(9, closure_state="open")
        deja.followup_date = fields.Datetime.now() - timedelta(days=5)
        neuve = self._session(3, closure_state="open")
        with patch.object(module_session, "FOLLOWUP_BATCH", 1):
            self.env["claude.chat.session"]._cron_closure_followup()
        self.assertTrue(self._relances(neuve))
        self.assertFalse(self._relances(deja))

    def test_la_relance_fait_remonter_le_marquage_non(self):
        ouverte = self._session(3, closure_state="open")
        muette = self._session(3)
        avant = {s: s.list_date for s in (ouverte, muette)}
        self.env["claude.chat.session"]._cron_closure_followup()
        self.assertGreater(ouverte.list_date, avant[ouverte])
        self.assertEqual(muette.list_date, avant[muette])
        self.assertEqual(muette.closure_state, "idle")

    def test_ce_que_la_passe_ne_touche_pas(self):
        recente = self._session(1, closure_state="open")
        idee = self._session(5, closure_state="ideation")
        faite = self._session(5, closure_state="done")
        passe = self._session(5, closure_state="open", origin="refine_meeting")
        en_cours = self._session(5, closure_state="open")
        self.env["claude.chat.message"].sudo().create({
            "session_id": en_cours.id, "role": "assistant", "content": "…",
            "state": "pending"})
        self.env["claude.chat.session"]._cron_closure_followup()
        for session in (recente, idee, faite, passe, en_cours):
            self.assertFalse(self._relances(session), session.closure_state)
            self.assertTrue(session.active)
        self.assertEqual(faite.closure_state, "done")

    def test_jamais_jugee_dort_sans_message(self):
        muette = self._session(4)
        self.env["claude.chat.session"]._cron_closure_followup()
        self.assertEqual(muette.closure_state, "idle")
        self.assertFalse(self._relances(muette))
        self.assertTrue(muette.active)

    def test_reglage_eteint(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_claude_chat.closure_enabled", "False")
        ouverte = self._session(5, closure_state="open")
        self.assertEqual(self.env["claude.chat.session"]._cron_closure_followup(), 0)
        self.assertFalse(self._relances(ouverte))

    def test_la_relance_parle_la_langue_de_la_personne(self):
        self.env["res.lang"]._activate_lang("fr_CA")
        self.user.lang = "fr_CA"
        attend = self._session(3, closure_state="waiting")
        self.env["claude.chat.session"]._cron_closure_followup()
        texte = self._relances(attend).content
        self.assertTrue(texte.startswith("🔔"))
        self.assertNotIn("waiting for you", texte)


@tagged("post_install", "-at_install")
class TestRoutes(HttpCase):

    def setUp(self):
        super().setUp()
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_claude_chat.closure_enabled", "True")
        self.moi = new_test_user(self.env, login="banc_routes", password="banc_routes")
        self.autre = new_test_user(self.env, login="banc_autre")
        Session = self.env["claude.chat.session"]
        self.mienne = Session.create({"name": "Mienne", "user_id": self.moi.id,
                                      "closure_state": "done", "closure_reason": "livré"})
        self.calme = Session.create({"name": "Calme", "user_id": self.moi.id,
                                     "closure_state": "ideation"})
        self.sienne = Session.create({"name": "Sienne", "user_id": self.autre.id,
                                      "closure_state": "done"})
        self.authenticate("banc_routes", "banc_routes")

    def _json(self, route, **params):
        return self.make_jsonrpc_request(route, params)

    def test_le_chemin_sans_flux_est_un_tour_comme_un_autre(self):
        """« Stream responses » éteint : `/claude-chat/send`. La dernière activité
        suit, et une réponse réduite à la balise ne la remet jamais à l'écran."""
        from odoo.addons.bf_ai_bridge.tools import transport
        self.env["ir.config_parameter"].sudo().set_param("bf_claude_chat.streaming", "False")
        quand = fields.Datetime.now() - timedelta(days=5)
        self.calme.write({"last_activity": quand, "list_date": quand})
        reponse_pont = {"response": '<closure state="open">suite</closure>', "session_id": "bf:x"}
        with patch.object(transport, "post", return_value=reponse_pont):
            r = self._json("/claude-chat/send", session_id=self.calme.id, message="Une question")
        self.assertEqual(r["response"], "(No response)")
        self.calme.invalidate_recordset()
        self.assertGreater(self.calme.last_activity, quand)
        self.assertGreater(self.calme.list_date, quand)
        self.assertNotIn("<closure", self.calme.message_ids.mapped("content")[-1])

    def test_liste_a_suivre_et_reponses(self):
        tout = self._json("/claude-chat/sessions")
        self.assertTrue(tout["closure_enabled"])
        lignes = {s["id"]: s for s in tout["sessions"]}
        self.assertEqual(lignes[self.mienne.id]["closure_state"], "done")
        self.assertIn(self.calme.id, lignes)
        a_suivre = self._json("/claude-chat/sessions", to_follow=True)
        self.assertEqual([s["id"] for s in a_suivre["sessions"]], [self.mienne.id])
        messages = self._json("/claude-chat/messages", session_id=self.mienne.id)
        self.assertEqual(messages["closure_state"], "done")
        # La conversation d'une autre personne est introuvable, pas interdite :
        # un refus dirait qu'elle existe.
        for route, params in (("/claude-chat/closure-answer", {"answer": "archive"}),
                              ("/claude-chat/link-answer", {"accept": True})):
            refus = self._json(route, session_id=self.sienne.id, **params)
            self.assertEqual(refus.get("error"), "Session not found", route)
        self.assertTrue(self.sienne.active)
        fait = self._json("/claude-chat/closure-answer", session_id=self.mienne.id,
                          answer="archive")
        self.assertEqual(fait["status"], "ok")
        self.assertFalse(self.mienne.active)
        # « Annuler » : la sienne revient, celle d'un autre reste introuvable.
        self.assertEqual(self._json("/claude-chat/restore-session",
                                    session_id=self.mienne.id)["status"], "ok")
        self.assertTrue(self.mienne.active)
        self.sienne.active = False
        refus = self._json("/claude-chat/restore-session", session_id=self.sienne.id)
        self.assertEqual(refus.get("error"), "Session not found")
        self.assertFalse(self.sienne.active)


@tagged("post_install", "-at_install")
class TestRoutesMobile(HttpCase):
    """api 7 : l'appli lit l'état et répond, sur ses seules conversations."""

    def setUp(self):
        super().setUp()
        Device = next((self.env[m] for m in ("bf.email.mobile.device",
                                             "sms.archive.mobile.device")
                       if m in self.env), None)
        if Device is None:
            self.skipTest("aucun modèle d'appareil mobile sur cette base")
        ICP = self.env["ir.config_parameter"].sudo()
        ICP.set_param("bf_claude_chat.enabled", "True")
        ICP.set_param("bf_claude_chat.closure_enabled", "True")
        self.moi = new_test_user(self.env, login="banc_mobile",
                                 groups="base.group_user,project.group_project_user")
        self.autre = new_test_user(self.env, login="banc_mobile_autre")
        self.jeton = Device._issue(self.moi.id, name="Banc").device_token
        projet = self.env["project.project"].create({
            "name": "Mobile", "privacy_visibility": "employees"})
        self.tache = self.env["project.task"].create(
            {"name": "Tâche mobile", "project_id": projet.id})
        Session = self.env["claude.chat.session"]
        self.faite = Session.create({"name": "Faite", "user_id": self.moi.id,
                                     "closure_state": "done", "closure_reason": "livré"})
        self.proposee = Session.create({"name": "Proposée", "user_id": self.moi.id,
                                        "closure_state": "open",
                                        "link_task_id": self.tache.id, "link_proposed": True})
        self.sienne = Session.create({"name": "Sienne", "user_id": self.autre.id,
                                      "closure_state": "done"})

    def _appel(self, route, body=None):
        entetes = {"Authorization": "Bearer %s" % self.jeton}
        url = "/bf_claude_chat/mobile/v1" + route
        if body is None:
            return self.url_open(url, headers=entetes, timeout=30)
        entetes["Content-Type"] = "application/json"
        return self.url_open(url, data=json.dumps(body), headers=entetes, timeout=30)

    def test_l_appli_lit_l_etat_et_repond(self):
        # Au moins 7 : l'api 8 garde tout ce que la 7 apporte.
        self.assertGreaterEqual(self._appel("/ping").json()["api"], 7)
        corps = self._appel("/sessions").json()
        self.assertTrue(corps["closure_enabled"])
        rangs = {r["id"]: r for r in corps["sessions"]}
        self.assertEqual(rangs[self.faite.id]["closure_state"], "done")
        self.assertEqual(rangs[self.proposee.id]["link_task"]["name"], "Tâche mobile")
        suivre = self._appel("/sessions?follow=1").json()["sessions"]
        self.assertEqual([r["id"] for r in suivre], [self.faite.id])
        messages = self._appel(f"/messages?session_id={self.faite.id}").json()
        self.assertEqual(messages["closure_reason"], "livré")
        lie = self._appel("/link-answer", {"session_id": self.proposee.id, "accept": True}).json()
        self.assertEqual((lie["res_model"], lie["res_id"]), ("project.task", self.tache.id))
        self.assertEqual(self._appel("/closure-answer", {"session_id": self.faite.id,
                                                         "answer": "nimporte"}).status_code, 400)
        fait = self._appel("/closure-answer", {"session_id": self.faite.id, "answer": "archive"})
        self.assertTrue(fait.json()["ok"])
        self.faite.invalidate_recordset()
        self.assertFalse(self.faite.active)

    def test_reparler_dans_une_conversation_archivee_la_ramene(self):
        self.faite.active = False
        with patch.object(turns, "start_runner"):
            reponse = self._appel("/ask", {"session_id": self.faite.id, "message": "Encore une chose"})
        self.assertEqual(reponse.status_code, 200)
        self.faite.invalidate_recordset()
        self.assertTrue(self.faite.active)

    def test_la_conversation_d_un_autre_est_introuvable(self):
        for route, corps in (("/closure-answer", {"answer": "archive"}),
                             ("/link-answer", {"accept": True})):
            reponse = self._appel(route, dict(corps, session_id=self.sienne.id))
            self.assertEqual(reponse.status_code, 404, route)
        self.sienne.invalidate_recordset()
        self.assertTrue(self.sienne.active)


@tagged("post_install", "-at_install")
class TestNotificationDuJour(_Base):
    """La notification du jour : ce qu'elle compte, ce qu'elle dit, quand elle se tait."""

    def setUp(self):
        super().setUp()
        if "sms.archive.unifiedpush" not in self.env:
            self.skipTest("pas de transport UnifiedPush sur cette base")
        self.envois = []
        patcher = patch.object(type(self.env["sms.archive.unifiedpush"]), "_send",
                               lambda _self, user, payload: self.envois.append((user, payload)))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _conv(self, etat, **vals):
        return self.env["claude.chat.session"].create(dict(
            {"name": "Conv", "user_id": self.user.id, "closure_state": etat}, **vals))

    def test_compte_et_texte(self):
        self.env["res.lang"]._activate_lang("fr_CA")
        self.user.lang = "fr_CA"
        self._conv("done"), self._conv("idle"), self._conv("waiting")
        self._conv("open", followup_date=fields.Datetime.now())
        self._conv("open"), self._conv("ideation")
        Session = self.env["claude.chat.session"]
        self.assertEqual(Session._closure_counts(self.user),
                         {"fermer": 2, "attend": 1, "relance": 1})
        self.assertTrue(Session._push_closure_summary(self.user))
        user, charge = self.envois[0]
        self.assertEqual(user, self.user)
        self.assertEqual((charge["type"], charge["count"]), ("genfox_follow", 4))
        self.assertEqual(charge["body"], "2 à fermer · 1 t'attend · 1 relancée")

    def test_une_seule_par_jour(self):
        self._conv("done")
        Session = self.env["claude.chat.session"]
        self.assertTrue(Session._push_closure_summary(self.user))
        self.assertFalse(Session._push_closure_summary(self.user),
                         "inscrite à deux courriels, elle ne la reçoit qu'une fois")
        self.assertEqual(len(self.envois), 1)
        self.user.sudo().gen_closure_push_date = fields.Date.today() - timedelta(days=1)
        self.assertTrue(Session._push_closure_summary(self.user))

    def test_rien_ne_part_quand_rien_n_attend(self):
        self._conv("ideation"), self._conv("open")
        self.assertFalse(self.env["claude.chat.session"]._push_closure_summary(self.user))
        self.assertFalse(self.envois)

    def test_reglage_eteint(self):
        self._conv("done")
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_claude_chat.closure_enabled", "False")
        self.assertFalse(self.env["claude.chat.session"]._push_closure_summary(self.user))
        self.assertFalse(self.envois)
