"""La création rejouée par la file hors ligne de l'app.

Une tâche ou un événement créé hors ligne, dont la réponse s'est perdue,
revient avec le MÊME ``client_uuid`` : il doit retrouver la première réponse,
pas créer un doublon.
"""

import json
import uuid

from odoo.tests import HttpCase, tagged

BASE = "/bf_calendar/mobile/v1"


@tagged("post_install", "-at_install")
class TestCreationRejouee(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        groupes = [cls.env.ref("base.group_user").id,
                   cls.env.ref("project.group_project_user").id]

        def usager(login):
            return cls.env["res.users"].create({
                "name": login, "login": login, "email": "%s@example.org" % login,
                "groups_id": [(6, 0, groupes)],
            })

        cls.user = usager("agenda.rejeu.t")
        cls.autre = usager("agenda.rejeu.autre.t")
        cls.projet = cls.env["project.project"].sudo().create({"name": "Banc rejeu"})
        Device = cls.env["bf.email.mobile.device"]
        cls.jeton = Device._issue(cls.user.id, name="Banc rejeu").device_token
        cls.jeton_autre = Device._issue(cls.autre.id, name="Banc rejeu autre").device_token

    def _post(self, route, charge, jeton=None):
        return self.url_open(
            BASE + route, data=json.dumps(charge),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer %s" % (jeton or self.jeton)}, timeout=30)

    def _taches(self, nom):
        return self.env["project.task"].sudo().search_count([("name", "=", nom)])

    def _evenements(self, nom):
        return self.env["calendar.event"].sudo().search_count([("name", "=", nom)])

    def _tache(self, nom, cle=None, jeton=None):
        charge = {"name": nom, "project_id": self.projet.id}
        if cle is not None:
            charge["client_uuid"] = cle
        return self._post("/task/create", charge, jeton)

    def _evenement(self, nom, cle=None):
        charge = {"name": nom, "start": "2026-10-05 14:00:00", "stop": "2026-10-05 15:00:00"}
        if cle is not None:
            charge["client_uuid"] = cle
        return self._post("/event/create", charge)

    def test_tache_rejouee_n_est_creee_qu_une_fois(self):
        cle = str(uuid.uuid4())
        a = self._tache("Tâche rejouée", cle)
        b = self._tache("Tâche rejouée", cle)
        self.assertEqual((a.status_code, b.status_code), (200, 200), a.text)
        self.assertEqual(self._taches("Tâche rejouée"), 1)
        self.assertEqual(b.json(), dict(a.json(), replay=True))

    def test_evenement_rejoue_n_est_cree_qu_une_fois(self):
        cle = str(uuid.uuid4())
        a = self._evenement("Rencontre rejouée", cle)
        b = self._evenement("Rencontre rejouée", cle)
        self.assertEqual((a.status_code, b.status_code), (200, 200), a.text)
        self.assertEqual(self._evenements("Rencontre rejouée"), 1)
        self.assertEqual(b.json(), dict(a.json(), replay=True))

    def test_sans_client_uuid_rien_ne_change(self):
        self._tache("Tâche sans garde")
        b = self._tache("Tâche sans garde")
        self.assertEqual(self._taches("Tâche sans garde"), 2)
        self.assertNotIn("replay", b.json())
        self._evenement("Rencontre sans garde")
        self._evenement("Rencontre sans garde")
        self.assertEqual(self._evenements("Rencontre sans garde"), 2)

    def test_uuid_invalide_est_un_400(self):
        r = self._tache("Tâche invalide", "abc")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"], "invalid_client_uuid")
        self.assertEqual(self._taches("Tâche invalide"), 0)
        r = self._evenement("Rencontre invalide", "abc")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self._evenements("Rencontre invalide"), 0)

    def test_le_uuid_d_un_autre_usager_ne_rend_pas_sa_reponse(self):
        cle = str(uuid.uuid4())
        a = self._tache("Tâche cloisonnée", cle)
        b = self._tache("Tâche cloisonnée", cle, jeton=self.jeton_autre)
        self.assertEqual(b.status_code, 200, b.text)
        self.assertNotIn("replay", b.json())
        self.assertNotEqual(a.json()["task"]["id"], b.json()["task"]["id"])
        self.assertEqual(self._taches("Tâche cloisonnée"), 2)

    def test_un_refus_ne_pose_pas_d_accuse(self):
        cle = str(uuid.uuid4())
        refus = self._post("/task/create", {"name": "Sans projet", "client_uuid": cle})
        self.assertEqual(refus.status_code, 400)
        reprise = self._tache("Avec projet", cle)
        self.assertEqual(reprise.status_code, 200, reprise.text)
        self.assertNotIn("replay", reprise.json())

    def test_meme_uuid_sur_une_autre_route_est_refuse(self):
        cle = str(uuid.uuid4())
        self.assertEqual(self._tache("Tâche d'abord", cle).status_code, 200)
        r = self._evenement("Rencontre ensuite", cle)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self._evenements("Rencontre ensuite"), 0)
