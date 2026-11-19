"""Le bloc-notes sur le bureau, éprouvé au modèle.

Trois ajouts, et c'est le premier qui porte tout le risque : la liste peut
désormais rendre les notes QUE DES COLLÈGUES ONT PARTAGÉES. Un filtre trop
large ici, et un widget de bureau affiche les notes privées de tout le monde.

Les deux autres — l'empreinte et la liste d'identifiants — sont ce qui permet
à un widget de rester juste sans tout relire : ils doivent donc filtrer
exactement comme la liste, sous peine de trahir par leur compte ce qu'ils ne
montrent pas.
"""
from datetime import timedelta

from odoo import fields
from odoo.tests.common import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install", "bf_bloc_notes")
class TestBlocNotesBureau(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        groupes = "base.group_user,project.group_project_user"
        cls.alice = new_test_user(cls.env, login="alice_bureau", groups=groupes,
                                  email="alicebureau@example.com")
        cls.bruno = new_test_user(cls.env, login="bruno_bureau", groups=groupes,
                                  email="brunobureau@example.com")
        cls.NoteA = cls.env["bf.note"].with_user(cls.alice)
        cls.NoteB = cls.env["bf.note"].with_user(cls.bruno)

        cls.a_privee = cls.NoteA.create({"body": "<p>Secret d'Alice</p>"})
        cls.a_partagee = cls.NoteA.create({"body": "<p>Alice partage</p>",
                                           "is_shared": True})
        cls.b_privee = cls.NoteB.create({"body": "<p>Secret de Bruno</p>"})

    def _ids(self, resultat):
        return {n["id"] for n in resultat["notes"]}

    def _ancre(self, notes, instant):
        """Pose la même date de modification sur plusieurs notes.

        🔴 Indispensable depuis que la marque d'eau vient de l'horloge de
        POSTGRES : dans un essai, `cr.now()` est figé au début de la
        transaction, et les `write_date` valent cette même heure. La marque
        reculée du recouvrement est donc TOUJOURS antérieure aux notes, et la
        sonde répondrait « quelque chose a changé » quoi qu'on fasse. Un essai
        qui veut éprouver « rien n'a bougé » doit donc placer les dates
        lui-même.
        """
        for note in notes:
            self._ecrit_a(note, instant)

    def _ecrit_a(self, note, instant):
        """Pose la date de modification d'une note, en SQL.

        🔴 Pourquoi un essai ne peut PAS se contenter d'écrire la note et de
        regarder l'heure : Odoo pose `write_date` avec le `now()` de Postgres,
        qui est figé au début de la TRANSACTION, alors que
        `fields.Datetime.now()` rend l'heure réelle. Un essai tient dans une
        seule transaction : toutes les notes qu'il écrit portent donc l'heure
        de son démarrage, déjà vieille de quelques secondes quand l'assertion
        tombe. L'essai mesurerait l'écart entre deux horloges au lieu de la
        règle qu'il prétend éprouver.

        ⚠️ Cet écart n'est pas qu'un artefact d'essai : en production,
        `write_date` vient de l'horloge de Postgres et la marque d'eau de celle
        du serveur Odoo. C'est une raison de plus pour que le recouvrement
        existe, et pour qu'il ne soit pas d'une microseconde.
        """
        self.env.cr.execute(
            "UPDATE bf_note SET write_date = %s WHERE id = %s",
            (instant, note.id))
        note.invalidate_recordset(["write_date"])

    # ── Ce que la liste rend, et surtout ce qu'elle ne rend pas ───────────

    def test_sans_shared_rien_ne_change(self):
        """Le contrat du téléphone est intact : ses notes, et rien d'autre."""
        vus = self._ids(self.NoteA._mobile_list())
        self.assertEqual(vus, {self.a_privee.id, self.a_partagee.id})
        self.assertNotIn(self.b_privee.id, vus)

    def test_avec_shared_la_note_partagee_d_un_collegue_apparait(self):
        b_partagee = self.NoteB.create({"body": "<p>Bruno partage</p>",
                                        "is_shared": True})
        vus = self._ids(self.NoteA._mobile_list(shared=True))
        self.assertIn(b_partagee.id, vus)
        self.assertIn(self.a_privee.id, vus, "les siennes restent là")

    def test_avec_shared_la_note_privee_d_un_collegue_reste_invisible(self):
        """🔴 L'essai qui compte : `shared` élargit aux PARTAGÉES, pas à tout."""
        vus = self._ids(self.NoteA._mobile_list(shared=True))
        self.assertNotIn(self.b_privee.id, vus)

    def test_la_note_d_un_collegue_s_annonce_comme_telle(self):
        b_partagee = self.NoteB.create({"body": "<p>Bruno partage</p>",
                                        "is_shared": True})
        notes = {n["id"]: n for n in
                 self.NoteA._mobile_list(shared=True)["notes"]}
        self.assertFalse(notes[b_partagee.id]["mine"],
                         "le widget doit pouvoir la mettre à part")
        self.assertTrue(notes[b_partagee.id]["shared"])
        self.assertTrue(notes[self.a_privee.id]["mine"])

    def test_je_n_ecris_pas_la_note_partagee_d_un_collegue(self):
        """Lecture seule : la règle du module."""
        b_partagee = self.NoteB.create({"body": "<p>Bruno partage</p>",
                                        "is_shared": True})
        sienne = b_partagee.with_user(self.alice)
        with self.assertRaises(Exception):
            sienne._mobile_update({"text": "réécrit par Alice"})
        self.assertIn("Bruno partage", b_partagee.body)

    # ── La fuite que la règle « admin tout voir » ouvrait ─────────────────

    def test_un_administrateur_ne_voit_pas_les_notes_privees_d_autrui(self):
        """🔴 L'essai qui manquait, et la fuite qu'il a fallu fermer.

        Ce module a une règle pour `base.group_system` dont le domaine est
        `[(1, '=', 1)]`, et les règles d'un même modèle se combinent en OU.
        Tant que `_mobile_list(shared=True)` laissait tomber le filtre sur
        l'auteur « pour laisser la règle décider », tout compte administrateur
        recevait par cette API TOUTES les notes de la base.

        ⚠️ Tous les autres essais d'isolation du module emploient des comptes
        `base.group_user`, chez qui la fuite ne paraît pas : c'est pour cela
        qu'elle a passé toute la suite au vert.
        """
        admin = new_test_user(
            self.env, login="admine_bureau",
            groups="base.group_user,base.group_system,project.group_project_user",
            email="adminebureau@example.com")
        NoteAdmin = self.env["bf.note"].with_user(admin)

        # La règle lui donne bien tout, par l'ORM : c'est voulu côté Odoo.
        self.assertIn(self.b_privee, NoteAdmin.search([]),
                      "la règle admin existe toujours, on ne la retire pas")

        # Mais l'API, elle, ne rend que ce que le bureau doit voir.
        vus = self._ids(NoteAdmin._mobile_list(shared=True))
        self.assertNotIn(self.b_privee.id, vus,
                         "la note privée d'un collègue ne sort pas de l'API")
        self.assertIn(self.a_partagee.id, vus,
                      "mais une note partagée, oui")

        ids = NoteAdmin._mobile_visible_ids(shared=True)["ids"]
        self.assertNotIn(self.b_privee.id, ids,
                         "ni par la liste d'identifiants")

        # Et la sonde ne doit pas trahir l'activité des notes privées.
        vieux = fields.Datetime.now() - timedelta(hours=2)
        self._ancre([self.a_privee, self.a_partagee, self.b_privee], vieux)
        marque = self.NoteA._mobile_datetime(vieux + timedelta(minutes=1))
        depart = NoteAdmin._mobile_stamp(shared=True, since=marque)

        nouvelle = self.NoteB.create({"body": "<p>Un autre secret de Bruno</p>"})
        self._ecrit_a(nouvelle, fields.Datetime.now())
        apres = NoteAdmin._mobile_stamp(shared=True, since=marque)
        self.assertFalse(apres["changed"], "ni par la sonde")
        self.assertEqual(depart["count"], apres["count"], "ni par le compte")

    def test_un_administrateur_ne_voit_pas_ses_propres_notes_en_trop(self):
        """Sans `shared`, un admin a exactement ses notes : rien de plus."""
        admin = new_test_user(
            self.env, login="admine2_bureau",
            groups="base.group_user,base.group_system",
            email="admine2_bureau@example.com")
        vus = self._ids(self.env["bf.note"].with_user(admin)._mobile_list())
        self.assertEqual(vus, set(), "cet admin n'a aucune note à lui")

    # ── Le plafond de la liste d'identifiants ─────────────────────────────

    def test_la_liste_d_identifiants_annonce_sa_troncature(self):
        """Un client ne doit jamais purger son écran sur une liste partielle."""
        resultat = self.NoteA._mobile_visible_ids()
        self.assertIn("truncated", resultat)
        self.assertFalse(resultat["truncated"])

    # ── L'empreinte ───────────────────────────────────────────────────────

    def test_la_sonde_voit_un_changement_posterieur_a_la_marque(self):
        depart = self.NoteA._mobile_stamp()
        self.a_privee.with_user(self.alice).write({"name": "Retouchée"})
        self._ecrit_a(self.a_privee, fields.Datetime.now())
        apres = self.NoteA._mobile_stamp(since=depart["next_since"])
        self.assertTrue(apres["changed"])

    def test_la_sonde_se_tait_quand_rien_n_a_bouge(self):
        """Sinon le widget tirerait le delta sans fin, pour rien."""
        vieux = fields.Datetime.now() - timedelta(hours=2)
        self._ancre([self.a_privee, self.a_partagee], vieux)
        marque = self.NoteA._mobile_datetime(vieux + timedelta(minutes=1))
        self.assertFalse(self.NoteA._mobile_stamp(since=marque)["changed"])

    def test_la_sonde_rattrape_une_ecriture_de_la_meme_seconde(self):
        """🔴 Le trou que le recouvrement ferme.

        `fields.Datetime.now()` d'Odoo tronque les microsecondes : une note
        écrite pendant la seconde même où le serveur a rendu son heure porte
        une `write_date` ÉGALE à cette heure, et `write_date > since` serait
        faux. Avec la marque reculée, elle est vue.
        """
        sonde = self.NoteA._mobile_stamp()
        # On simule l'écriture « dans la même seconde » en posant la date de
        # modification exactement à l'heure que le serveur venait de rendre.
        self._ecrit_a(self.a_privee,
                      self.NoteA._mobile_parse_datetime(sonde["server_time"]))

        naif = self.NoteA._mobile_stamp(since=sonde["server_time"])
        self.assertFalse(naif["changed"],
                         "avec l'heure nue, l'écriture est invisible : "
                         "c'est bien le trou qu'on décrit")
        avec_marge = self.NoteA._mobile_stamp(since=sonde["next_since"])
        self.assertTrue(avec_marge["changed"],
                        "avec la marque reculée, elle est rattrapée")

    def test_la_marque_d_eau_recule_sous_l_heure_du_serveur(self):
        sonde = self.NoteA._mobile_stamp()
        serveur = self.NoteA._mobile_parse_datetime(sonde["server_time"])
        marque = self.NoteA._mobile_parse_datetime(sonde["next_since"])
        self.assertEqual((serveur - marque).total_seconds(),
                         self.NoteA.OVERLAP_SECONDS)

    def test_le_delta_rend_la_marque_a_garder(self):
        """Un client n'a jamais à calculer le recouvrement lui-même."""
        resultat = self.NoteA._mobile_list()
        self.assertIn("next_since", resultat)
        self.assertLess(resultat["next_since"], resultat["server_time"])

    def test_la_sonde_signale_une_suppression_par_le_compte(self):
        """🔴 Une suppression ne déplace AUCUNE date : `changed` reste faux,
        et c'est le compte — rendu pour cela — qui la dénonce."""
        jetable = self.NoteA.create({"body": "<p>À jeter</p>"})
        depart = self.NoteA._mobile_stamp()
        jetable.unlink()
        apres = self.NoteA._mobile_stamp(since=depart["next_since"])
        self.assertNotEqual(depart["count"], apres["count"])

    def test_la_sonde_ignore_les_notes_privees_d_autrui(self):
        """🔴 Sans quoi la sonde trahirait l'activité des autres : un widget
        qui se réveille chaque fois qu'un collègue écrit une note PRIVÉE sait
        quand il travaille, et sur quel rythme."""
        vieux = fields.Datetime.now() - timedelta(hours=2)
        self._ancre([self.a_privee, self.a_partagee, self.b_privee], vieux)
        marque = self.NoteA._mobile_datetime(vieux + timedelta(minutes=1))

        depart = self.NoteA._mobile_stamp(shared=True, since=marque)
        self.assertFalse(depart["changed"], "rien n'a bougé depuis la marque")

        nouvelle = self.NoteB.create({"body": "<p>Encore un secret de Bruno</p>"})
        self._ecrit_a(nouvelle, fields.Datetime.now())
        apres = self.NoteA._mobile_stamp(shared=True, since=marque)
        self.assertFalse(apres["changed"],
                         "une note PRIVÉE d'autrui ne réveille pas le bureau")
        self.assertEqual(depart["count"], apres["count"],
                         "et ne se compte pas")

    def test_la_sonde_bouge_quand_un_collegue_partage(self):
        depart = self.NoteA._mobile_stamp(shared=True)
        partagee = self.NoteB.create({"body": "<p>Bruno partage enfin</p>",
                                      "is_shared": True})
        self._ecrit_a(partagee, fields.Datetime.now())
        apres = self.NoteA._mobile_stamp(shared=True,
                                         since=depart["next_since"])
        self.assertTrue(apres["changed"])

    def test_la_sonde_voit_un_archivage(self):
        """L'archivage est un changement que le bureau doit voir : c'est ainsi
        qu'une note archivée au téléphone quitte le poste."""
        depart = self.NoteA._mobile_stamp()
        self.a_privee.with_user(self.alice).action_archive()
        self._ecrit_a(self.a_privee, fields.Datetime.now())
        apres = self.NoteA._mobile_stamp(since=depart["next_since"])
        self.assertTrue(apres["changed"])
        self.assertEqual(depart["count"], apres["count"],
                         "une archivée reste comptée, elle n'a pas disparu")

    # ── La liste d'identifiants ───────────────────────────────────────────

    def test_les_identifiants_visibles_couvrent_les_archivees(self):
        self.a_privee.with_user(self.alice).action_archive()
        ids = self.NoteA._mobile_visible_ids()["ids"]
        self.assertIn(self.a_privee.id, ids,
                      "une archivée est encore à nous, le bureau la garde")

    def test_les_identifiants_visibles_taisent_les_notes_d_autrui(self):
        ids = self.NoteA._mobile_visible_ids(shared=True)["ids"]
        self.assertNotIn(self.b_privee.id, ids)

    def test_une_note_departagee_quitte_la_liste_d_identifiants(self):
        """🔴 Le cas que `since` ne peut pas annoncer.

        Bruno cesse de partager : la note devient illisible pour Alice, donc
        aucun delta ne la mentionnera jamais plus. C'est cette liste, et elle
        seule, qui dit au bureau de l'oublier.
        """
        b_partagee = self.NoteB.create({"body": "<p>Bruno partage</p>",
                                        "is_shared": True})
        self.assertIn(b_partagee.id,
                      self.NoteA._mobile_visible_ids(shared=True)["ids"])

        b_partagee.write({"is_shared": False})

        self.assertNotIn(b_partagee.id,
                         self.NoteA._mobile_visible_ids(shared=True)["ids"])
        # Et la preuve que le delta, lui, ne dit rien : il ne la rend pas non
        # plus, donc un client qui n'écoute que `since` la garderait pour
        # toujours.
        delta = self.NoteA._mobile_list(shared=True, since="1970-01-01 00:00:00")
        self.assertNotIn(b_partagee.id, self._ids(delta))

    def test_une_note_supprimee_quitte_la_liste_d_identifiants(self):
        jetable = self.NoteA.create({"body": "<p>À jeter</p>"})
        self.assertIn(jetable.id, self.NoteA._mobile_visible_ids()["ids"])
        jetable.unlink()
        self.assertNotIn(jetable.id, self.NoteA._mobile_visible_ids()["ids"])
