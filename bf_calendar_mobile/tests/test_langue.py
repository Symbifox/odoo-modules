"""Langue des refus, des notes et des libellés de l'agenda mobile.

La source du module est en anglais ; le français vit dans `i18n/fr_CA.po`.
Ce que ces essais prouvent :

* les refus rendus à l'application (fenêtre trop large ou inversée, état de
  présence inconnu, rencontre à laquelle on ne participe pas) sont dans la
  langue de l'usager ;
* les routes mobiles, publiques et authentifiées par jeton d'appareil, n'ont
  pas de langue de session : elles prennent celle de l'usager de l'appareil,
  si bien que la note laissée au fil d'une rencontre (réponse, participants
  ajoutés ou retirés) est dans la langue de son auteur ;
* les libellés de sélection rendus à l'application (état et priorité d'une
  tâche) sont traduits, et non la source anglaise pour tout le monde.

Les essais épinglent fr_CA et en_CA, qu'ils activent, et créent leurs usagers
avec une langue explicite : la langue par défaut de la base n'est pas connue
d'avance.
"""

import json

from odoo.exceptions import UserError
from odoo.tests import HttpCase, TransactionCase, tagged

BASE = "/bf_calendar/mobile/v1"


def _langues(env):
    for code in ("fr_CA", "en_CA"):
        env["res.lang"]._activate_lang(code)
    env["ir.module.module"]._load_module_terms(
        ["project", "calendar", "bf_calendar_mobile"], ["fr_CA"], overwrite=True)


def _usager(env, login, lang):
    return env["res.users"].create({
        "name": login, "login": login, "email": "%s@example.com" % login,
        "lang": lang,
        "groups_id": [(6, 0, [env.ref("base.group_user").id,
                              env.ref("project.group_project_user").id])],
    })


@tagged("post_install", "-at_install", "bf_calendar_mobile")
class TestLangueAgendaMobile(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _langues(cls.env)
        cls.fr = _usager(cls.env, "essai_fr", "fr_CA")
        cls.en = _usager(cls.env, "essai_en", "en_CA")
        cls.Event = cls.env["calendar.event"]

    # ------------------------------------------------------------------
    # Refus de la fenêtre (calendar_event.py:81, 83)
    # ------------------------------------------------------------------

    def test_fenetre_trop_large_dans_la_langue_de_l_usager(self):
        """« Fenêtre trop large » était écrit en dur : anglais pour l'anglophone."""
        with self.assertRaises(UserError) as en:
            self.Event.with_context(lang="en_CA")._mobile_window(
                "2026-01-01 00:00:00", "2026-06-01 00:00:00")
        self.assertIn("Window too wide (maximum", str(en.exception))
        with self.assertRaises(UserError) as fr:
            self.Event.with_context(lang="fr_CA")._mobile_window(
                "2026-01-01 00:00:00", "2026-06-01 00:00:00")
        self.assertIn("Fenêtre trop large (maximum", str(fr.exception))

    def test_fenetre_inversee_dans_la_langue_de_l_usager(self):
        with self.assertRaises(UserError) as en:
            self.Event.with_context(lang="en_CA")._mobile_window(
                "2026-02-01 00:00:00", "2026-01-01 00:00:00")
        self.assertEqual(str(en.exception), "The end of the window is before its start.")
        with self.assertRaises(UserError) as fr:
            self.Event.with_context(lang="fr_CA")._mobile_window(
                "2026-02-01 00:00:00", "2026-01-01 00:00:00")
        self.assertEqual(str(fr.exception), "La fin de la fenêtre précède son début.")

    # ------------------------------------------------------------------
    # Note des participants (calendar_event.py:557)
    # ------------------------------------------------------------------

    def _rencontre_a_trois(self, organisateur):
        self.ajoute = self.env["res.partner"].create({"name": "Contact ajouté"})
        self.retire = self.env["res.partner"].create({"name": "Contact retiré"})
        return self.Event.with_user(organisateur).create({
            "name": "Rencontre d'essai",
            "start": "2026-10-05 14:00:00", "stop": "2026-10-05 15:00:00",
            "user_id": organisateur.id,
            "partner_ids": [(6, 0, (organisateur.partner_id | self.retire).ids)],
        })

    def test_note_des_participants_separateur_anglais(self):
        """calendar_event.py:557 : le séparateur « ; » espacé à la française était
        en dur dans la note ; c'est maintenant une virgule, juste dans les deux
        langues."""
        event = self._rencontre_a_trois(self.en)
        event.with_user(self.en).with_context(lang="en_CA").mobile_set_attendees(
            add_ids=[self.ajoute.id], remove_ids=[self.retire.id])
        corps = str(event.message_ids[0].body)
        self.assertIn("essai_en added Contact ajouté, removed Contact retiré "
                      "from the mobile app", corps)
        self.assertNotIn(";", corps)

    def test_note_des_participants_en_francais(self):
        """La note de l'usager fr_CA sort en français, morceaux et phrase compris."""
        event = self._rencontre_a_trois(self.fr)
        event.with_user(self.fr).with_context(lang="fr_CA").mobile_set_attendees(
            add_ids=[self.ajoute.id], remove_ids=[self.retire.id])
        corps = str(event.message_ids[0].body)
        self.assertIn("essai_fr a ajouté Contact ajouté, a retiré Contact retiré "
                      "depuis l'application mobile", corps.replace("&#39;", "'"))

    # ------------------------------------------------------------------
    # Libellés de sélection rendus à l'app (project_task.py:72, 437, 441)
    # ------------------------------------------------------------------

    def _tache(self):
        projet = self.env["project.project"].create({"name": "Projet d'essai"})
        return self.env["project.task"].create({
            "name": "Tâche d'essai", "project_id": projet.id,
            "user_ids": [(6, 0, (self.fr | self.en).ids)],
            "state": "01_in_progress",
        })

    def test_etat_de_la_tache_dans_la_langue_de_l_usager(self):
        """`.selection` rend la source anglaise à tout le monde."""
        tache = self._tache()
        en = tache.with_user(self.en).with_context(lang="en_CA").mobile_detail()["task"]
        fr = tache.with_user(self.fr).with_context(lang="fr_CA").mobile_detail()["task"]
        self.assertEqual(en["state_label"], "In Progress")
        self.assertEqual(fr["state_label"], "En cours")

    def test_options_de_tache_dans_la_langue_de_l_usager(self):
        Task = self.env["project.task"]
        en = Task.with_user(self.en).with_context(lang="en_CA").mobile_options()
        fr = Task.with_user(self.fr).with_context(lang="fr_CA").mobile_options()
        etats_en = {s["value"]: s["label"] for s in en["states"]}
        etats_fr = {s["value"]: s["label"] for s in fr["states"]}
        self.assertEqual(etats_en["01_in_progress"], "In Progress")
        self.assertEqual(etats_fr["01_in_progress"], "En cours")
        prio_en = {p["value"]: p["label"] for p in en["priorities"]}
        prio_fr = {p["value"]: p["label"] for p in fr["priorities"]}
        self.assertNotEqual(prio_fr["1"], prio_en["1"],
                            "La priorité sort dans la source anglaise pour le francophone.")


@tagged("post_install", "-at_install", "bf_calendar_mobile")
class TestLangueRoutesAgenda(HttpCase):
    """Par la route à jeton : la langue vient de l'usager de l'appareil."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _langues(cls.env)
        cls.fr = _usager(cls.env, "essai_route_fr", "fr_CA")
        cls.en = _usager(cls.env, "essai_route_en", "en_CA")
        Device = cls.env["bf.email.mobile.device"]
        cls.jetons = {
            cls.fr.id: Device._issue(cls.fr.id, name="Appareil d'essai fr").device_token,
            cls.en.id: Device._issue(cls.en.id, name="Appareil d'essai en").device_token,
        }

    def _post(self, usager, route, charge):
        return self.url_open(
            BASE + route, data=json.dumps(charge),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer %s" % self.jetons[usager.id]},
            timeout=30)

    def _get(self, usager, route):
        return self.url_open(BASE + route, headers={
            "Authorization": "Bearer %s" % self.jetons[usager.id]}, timeout=30)

    def _rencontre(self, usager, participant=None):
        return self.env["calendar.event"].sudo().create({
            "name": "Rencontre route langue", "start": "2026-10-05 14:00:00",
            "stop": "2026-10-05 15:00:00", "user_id": usager.id,
            "partner_ids": [(6, 0, (participant or usager.partner_id).ids)],
        })

    def test_note_rsvp_dans_la_langue_de_l_auteur(self):
        """mobile_api.py:89 : sans la langue au contexte, les deux notes sortent
        dans la même langue (celle par défaut de la requête)."""
        for usager, attendu in ((self.fr, "a confirmé sa présence depuis l'application mobile"),
                                (self.en, "confirmed attendance from the mobile app")):
            event = self._rencontre(usager)
            rep = self._post(usager, "/rsvp", {"event_id": event.id, "state": "accepted"})
            self.assertEqual(rep.status_code, 200, rep.text)
            corps = str(event.message_ids[0].body).replace("&#39;", "'")
            self.assertIn(attendu, corps)

    def test_refus_d_etat_dans_la_langue_de_l_usager(self):
        """mobile_api.py:314 : « État de présence inconnu. » était en dur."""
        for usager, attendu in ((self.fr, "État de présence inconnu."),
                                (self.en, "Unknown attendance state.")):
            event = self._rencontre(usager)
            rep = self._post(usager, "/rsvp", {"event_id": event.id, "state": "peut-etre"})
            self.assertEqual(rep.status_code, 400, rep.text)
            self.assertEqual(rep.json()["detail"], attendu)

    def test_refus_non_participant_dans_la_langue_de_l_usager(self):
        """mobile_api.py:322 : l'organisateur absent de la liste des participants."""
        autre = self.env["res.partner"].create({"name": "Seul participant"})
        for usager, attendu in ((self.fr, "Vous ne participez pas à cette rencontre."),
                                (self.en, "You are not attending this meeting.")):
            event = self._rencontre(usager, participant=autre)
            rep = self._post(usager, "/rsvp", {"event_id": event.id, "state": "accepted"})
            self.assertEqual(rep.status_code, 400, rep.text)
            self.assertEqual(rep.json()["detail"], attendu)

    def test_refus_de_fenetre_par_la_route(self):
        """La langue posée par `_authed` atteint aussi les refus du modèle."""
        for usager, attendu in ((self.fr, "La fin de la fenêtre précède son début."),
                                (self.en, "The end of the window is before its start.")):
            rep = self._get(usager, "/events?from=2026-02-01%2000:00:00&to=2026-01-01%2000:00:00")
            self.assertEqual(rep.status_code, 400, rep.text)
            self.assertEqual(rep.json()["detail"], attendu)
