"""Isolation par personne de la couche mobile du bloc-notes 3.0.0.

La couche mobile doit ne rendre que les
notes de l'appelant (``user_id = uid``), sans sudo, et les noms de liens viennent
de ``res_name`` calculé sous les droits de l'appelant. Ces essais le mesurent
au lieu de le croire, par les deux portes : les méthodes du modèle (communes à
la page ``/notes`` et à Symbifox Mobile) et la route HTTP à jeton.

A et B : internes, utilisateurs de Projet, non administrateurs. A a un projet
PRIVÉ (abonnés seulement) et une tâche dedans : B ne peut pas les lire.
Données inventées ; les caches sont vidés avant chaque geste de B, sans quoi
un cache de la transaction masquerait un refus de droits.
"""
import json
import uuid

from odoo.exceptions import AccessError
from odoo.tests import HttpCase, new_test_user, tagged
from odoo.tests.common import TransactionCase

SECRET_TACHE = "Tache-privee-fictive"


class _Commun:

    @classmethod
    def _semer(cls):
        groupes = "base.group_user,project.group_project_user"
        cls.a = new_test_user(cls.env, login="mob_iso_a", groups=groupes,
                              name="Personne A mobile")
        cls.b = new_test_user(cls.env, login="mob_iso_b", groups=groupes,
                              name="Personne B mobile")
        cls.projet_prive = cls.env["project.project"].create({
            "name": "Projet privé fictif de A", "privacy_visibility": "followers",
            "user_id": cls.a.id, "message_partner_ids": [(6, 0, cls.a.partner_id.ids)],
        })
        cls.tache_privee = cls.env["project.task"].create({
            "name": SECRET_TACHE, "project_id": cls.projet_prive.id,
            "user_ids": [(6, 0, cls.a.ids)],
        })

    def _en(self, user, model):
        self.env.invalidate_all()
        return self.env[model].with_user(user)

    def _note(self, user, texte, partagee=False):
        res = self._en(user, "bf.note")._mobile_create(
            {"client_uuid": str(uuid.uuid4()), "text": texte})
        note = self.env["bf.note"].browse(res["note"]["id"])
        if partagee:
            self._en(user, "bf.note").browse(note.id).write({"is_shared": True})
        self.env.invalidate_all()
        return note


@tagged("post_install", "-at_install", "isolation_menage")
class TestIsolationMobileModele(_Commun, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._semer()

    def test_contre_epreuve_b_ne_lit_pas_la_tache_privee(self):
        with self.assertRaises(AccessError):
            self._en(self.b, "project.task").browse(self.tache_privee.id).read(["name"])

    def test_la_liste_ne_rend_que_les_notes_de_l_appelant(self):
        privee = self._note(self.a, "Privée de A")
        partagee = self._note(self.a, "Partagée par A", partagee=True)
        a_moi = self._note(self.b, "Note de B")
        for kw in ({}, {"archived": True}, {"since": "2000-01-01T00:00:00Z"},
                   {"query": "A"}):
            ids = {n["id"] for n in self._en(self.b, "bf.note")._mobile_list(**kw)["notes"]}
            self.assertNotIn(privee.id, ids, kw)
            self.assertNotIn(partagee.id, ids, "la liste mobile rend une note d'autrui %s" % kw)
        ids = {n["id"] for n in self._en(self.b, "bf.note")._mobile_list()["notes"]}
        self.assertIn(a_moi.id, ids, "contre-épreuve : B voit sa note")

    def test_ouvrir_par_id_suit_la_regle_de_lecture(self):
        """``_mobile_browse`` : une note privée d'autrui est introuvable ; une
        note PARTAGÉE d'autrui s'ouvre (règle de lecture du module, ``mine``
        faux) mais ne se modifie ni ne reçoit de geste."""
        privee = self._note(self.a, "Privée de A")
        partagee = self._note(self.a, "Partagée par A", partagee=True)
        N = self._en(self.b, "bf.note")
        self.assertIsNone(N._mobile_browse(privee.id))
        self.assertIsNone(N._mobile_browse(10 ** 9), "inexistante et interdite : même réponse")
        vue = N._mobile_browse(partagee.id)
        self.assertEqual(vue.id, partagee.id)
        self.assertFalse(vue._mobile_payload()["mine"])
        with self.assertRaises(AccessError):
            self._en(self.b, "bf.note").browse(partagee.id)._mobile_update({"text": "piraté"})
        with self.assertRaises(AccessError):
            self._en(self.b, "bf.note").browse(partagee.id)._mobile_action("archive")

    def test_nom_de_lien_vers_une_fiche_illisible_par_b_sa_propre_note(self):
        """B rattache SA note à la tâche privée de A (ids séquentiels) : le nom
        ne doit pas lui revenir, ni par la couche mobile ni par le champ."""
        note = self._note(self.b, "Note de B")
        try:
            with self.env.cr.savepoint():
                self._en(self.b, "bf.note.link").create({
                    "note_id": note.id, "res_model": "project.task",
                    "res_id": self.tache_privee.id})
        except AccessError:
            return  # refus à la création : rien ne peut fuir par là
        self.env.invalidate_all()
        charge = self._en(self.b, "bf.note").browse(note.id)._mobile_payload()
        noms = [l["name"] for l in charge["links"]]
        self.assertNotIn(SECRET_TACHE, " ".join(noms),
                         "la couche mobile rend à B le nom d'une tâche qu'il ne peut pas lire")
        lu = self._en(self.b, "bf.note.link").search_read(
            [("note_id", "=", note.id)], ["res_name"])
        self.assertNotIn(SECRET_TACHE, " ".join(str(r["res_name"]) for r in lu),
                         "res_name (calcul stocké) rend à B le nom d'une tâche illisible")

    def test_nom_de_lien_d_une_note_partagee_par_a(self):
        """A partage une note liée à SA tâche privée : B lit la note, mais le nom
        de la tâche, qu'il ne peut pas ouvrir, ne doit pas lui arriver."""
        note = self._note(self.a, "Partagée par A", partagee=True)
        self._en(self.a, "bf.note.link").create({
            "note_id": note.id, "res_model": "project.task",
            "res_id": self.tache_privee.id})
        self.env.invalidate_all()
        vue = self._en(self.b, "bf.note")._mobile_browse(note.id)
        self.assertTrue(vue)
        noms = [l["name"] for l in vue._mobile_payload()["links"]]
        self.assertNotIn(SECRET_TACHE, " ".join(noms),
                         "la couche mobile rend à B le nom d'une tâche qu'il ne peut pas lire")
        self.assertEqual(
            self._en(self.a, "bf.note").browse(note.id)._mobile_payload()["links"][0]["name"],
            SECRET_TACHE, "contre-épreuve : A voit le nom de sa tâche")


@tagged("post_install", "-at_install", "isolation_menage")
class TestIsolationMobileHttp(_Commun, HttpCase):
    """La route à jeton : l'environnement est celui de l'appareil, sans sudo."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._semer()

    def _jeton(self, user):
        """Un VRAI jeton d'appareil (registre SMS, premier de `_DEVICE_MODELS`) :
        la porte `_device()` n'est pas simulée."""
        if "sms.archive.mobile.device" not in self.env:
            return None
        appareil = self.env["sms.archive.mobile.device"].sudo()._issue(user.id, name="Appareil d'essai")
        return appareil.device_token

    def test_route_liste_et_note_par_id(self):
        from odoo.addons.bf_bloc_notes.controllers.mobile_api import BASE
        privee = self._note(self.a, "Privée de A HTTP")
        jeton = self._jeton(self.b)
        if not jeton:
            self.skipTest("aucun moyen d'émettre un jeton d'appareil sur ce banc")
        entetes = {"Authorization": "Bearer %s" % jeton}
        r = self.url_open(BASE + "/notes", headers=entetes)
        self.assertEqual(r.status_code, 200)
        ids = {n["id"] for n in r.json()["notes"]}
        self.assertNotIn(privee.id, ids)
        r = self.url_open(BASE + "/notes/%s" % privee.id, headers=entetes)
        self.assertEqual(r.status_code, 404)
        self.assertNotIn("Privée de A", r.text)
        # Le nom de la tâche privée de A, par une note de B liée à elle.
        note_b = self._note(self.b, "Note de B HTTP")
        self.env["bf.note.link"].create({"note_id": note_b.id, "res_model": "project.task",
                                         "res_id": self.tache_privee.id})
        r = self.url_open(BASE + "/notes/%s" % note_b.id, headers=entetes)
        self.assertEqual(r.status_code, 200)
        self.assertNotIn(SECRET_TACHE, r.text,
                         "la route mobile rend à B le nom d'une tâche qu'il ne peut pas lire")
