"""Isolation entre personnes, côté bloc-notes : ce qu'un lot de sécurité avait laissé ouvert.

1. Sur une note partagée, le nom d'une fiche liée se lit sous les droits de QUI
   LIT. Il était stocké, donc calculé pour l'auteur : un collègue relisait le
   nom d'une fiche que lui ne peut pas ouvrir, et la recherche par ce nom
   servait d'oracle.
2. Une activité posée sur la fiche liée ne recopie plus le corps de la note.
3. Une étiquette créée par une personne est à elle ; les communes se lisent
   par tous et ne se renomment que par l'administration.

Chaque refus est doublé du geste légitime qui doit rester permis.
Données inventées.
"""
from odoo.exceptions import AccessError
from odoo.tests import new_test_user, tagged
from odoo.tests.common import TransactionCase

SECRET = "SECRET-essai-fiche-de-A"


@tagged("post_install", "-at_install", "isolation_menage")
class TestIsolationNotesRestes(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        g = "base.group_user,project.group_project_user"
        cls.a = new_test_user(cls.env, login="notes_restes_a", groups=g)
        cls.b = new_test_user(cls.env, login="notes_restes_b", groups=g)
        # Une fiche que seule A lit : sa note privée.
        cls.fiche_a = cls.env["bf.note"].with_user(cls.a).create({"name": SECRET})
        cls.partagee = cls.env["bf.note"].with_user(cls.a).create(
            {"name": "Note partagée de A", "is_shared": True})
        cls.env["bf.note.link"].with_user(cls.a).create(
            {"note_id": cls.partagee.id, "res_model": "bf.note", "res_id": cls.fiche_a.id})

    def _en(self, user, model):
        self.env.invalidate_all()
        return self.env[model].with_user(user)

    # ── 1. Nom de la fiche liée ───────────────────────────────────

    def test_b_ne_lit_pas_le_nom_que_voit_a(self):
        note_b = self._en(self.b, "bf.note").browse(self.partagee.id)
        self.assertTrue(note_b.name, "B lit la note partagée")
        self.assertFalse(note_b.res_name)
        self.assertFalse(note_b.link_ids.res_name)
        lu = self._en(self.b, "bf.note").search_read([("id", "=", self.partagee.id)], ["res_name"])
        self.assertFalse(lu[0]["res_name"])

    def test_a_lit_toujours_le_nom(self):
        note_a = self._en(self.a, "bf.note").browse(self.partagee.id)
        self.assertEqual(note_a.res_name, SECRET)
        self.assertEqual(note_a.link_ids.res_name, SECRET)

    def test_la_recherche_par_nom_n_est_pas_un_oracle(self):
        self.assertFalse(self._en(self.b, "bf.note").search([("res_name", "ilike", SECRET)]))
        self.assertIn(self.partagee,
                      self._en(self.a, "bf.note").search([("res_name", "ilike", SECRET)]))

    def test_un_calcul_sudo_ne_fuit_pas(self):
        """Un environnement superutilisateur passé avant la lecture de B ne
        laisse pas de nom en cache."""
        self.assertEqual(self.partagee.sudo().res_name, SECRET)
        self.assertFalse(self.env["bf.note"].with_user(self.b).browse(self.partagee.id).res_name)

    # ── 2. Activité ───────────────────────────────────────────────

    def test_activite_sur_la_fiche_liee_sans_le_corps(self):
        projet = self.env["project.project"].create({"name": "Projet commun d'essai"})
        tache = self.env["project.task"].create({"name": "Tâche commune", "project_id": projet.id})
        note = self._en(self.a, "bf.note").create(
            {"name": "Rappel", "body": "<p>%s dans le corps</p>" % SECRET})
        self.env["bf.note.link"].with_user(self.a).create(
            {"note_id": note.id, "res_model": "project.task", "res_id": tache.id})
        activites = note.with_user(self.a)._create_activities_for_links(offset_days=1)
        self.assertEqual(activites.mapped("res_model"), ["project.task"])
        lu = self._en(self.b, "mail.activity").browse(activites.id).read(["note"])[0]["note"]
        self.assertNotIn(SECRET, str(lu))
        self.assertIn("/odoo/bf.note/%s" % note.id, str(lu))

    def test_activite_sur_la_note_garde_le_corps(self):
        note = self._en(self.a, "bf.note").create({"name": "Seule", "body": "<p>corps gardé</p>"})
        activites = note.with_user(self.a)._create_activities_for_links(offset_days=1)
        self.assertEqual(activites.res_model, "bf.note")
        self.assertIn("corps gardé", str(activites.note))

    # ── 3. Étiquettes ─────────────────────────────────────────────

    def test_etiquette_creee_est_personnelle(self):
        tag = self._en(self.a, "bf.note.tag").create({"name": "Divorce (essai)"})
        self.assertEqual(tag.user_id, self.a)
        self.assertFalse(self._en(self.b, "bf.note.tag").search([("id", "=", tag.id)]))
        for geste in ("read", "write", "unlink"):
            with self.subTest(geste=geste):
                with self.assertRaises(AccessError):
                    with self.env.cr.savepoint():
                        rec = self._en(self.b, "bf.note.tag").browse(tag.id)
                        if geste == "read":
                            rec.read(["name"])
                        elif geste == "write":
                            rec.write({"name": "renommée par B"})
                        else:
                            rec.unlink()
        self._en(self.a, "bf.note.tag").browse(tag.id).write({"name": "Divorce (essai) bis"})

    def test_b_ne_cree_pas_d_etiquette_commune_ni_au_nom_de_a(self):
        for proprietaire in (False, self.a.id):
            with self.subTest(proprietaire=proprietaire):
                with self.assertRaises(AccessError):
                    with self.env.cr.savepoint():
                        self._en(self.b, "bf.note.tag").create(
                            {"name": "forgée", "user_id": proprietaire})

    def test_le_proprietaire_d_une_etiquette_ne_change_pas(self):
        """Odoo ne relit pas la règle après un `write` ; B
        rendait SA étiquette commune, ou la posait chez A."""
        tag = self._en(self.b, "bf.note.tag").create({"name": "À B (essai)"})
        for proprietaire in (False, self.a.id):
            with self.subTest(proprietaire=proprietaire):
                with self.assertRaises(AccessError):
                    with self.env.cr.savepoint():
                        self._en(self.b, "bf.note.tag").browse(tag.id).write(
                            {"user_id": proprietaire})
        self.assertEqual(tag.user_id, self.b)
        admin = self.env.ref("base.user_admin")
        self._en(admin, "bf.note.tag").browse(tag.id).write({"user_id": False})
        self.assertFalse(tag.user_id)

    def test_etiquettes_communes_lues_par_tous_gerees_par_l_admin(self):
        commune = self.env.ref("bf_bloc_notes.tag_idee")
        self.assertFalse(commune.user_id)
        self.assertIn(commune, self._en(self.b, "bf.note.tag").search([]))
        with self.assertRaises(AccessError):
            with self.env.cr.savepoint():
                self._en(self.b, "bf.note.tag").browse(commune.id).write({"name": "renommée"})
        admin = self.env.ref("base.user_admin")
        self._en(admin, "bf.note.tag").browse(commune.id).write({"color": 5})
        self.assertEqual(commune.color, 5)

    def test_etiquette_d_autrui_absente_de_la_note_partagee(self):
        tag = self._en(self.a, "bf.note.tag").create({"name": "Perso de A (essai)"})
        self.partagee.with_user(self.a).write({"tag_ids": [(4, tag.id)]})
        self.assertFalse(self._en(self.b, "bf.note").browse(self.partagee.id).tag_ids)
        self.assertEqual(self._en(self.a, "bf.note").browse(self.partagee.id).tag_ids, tag)
