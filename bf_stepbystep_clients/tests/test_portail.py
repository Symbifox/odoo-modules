"""Le portail client de Progression.

Ce qui compte : le client voit ses étapes et la consigne de l'étape en cours,
jamais le dossier interne ; l'adresse ne s'ouvre qu'avec son jeton et tant que
le portail est publié ; seul un administrateur de projets publie.
"""
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import HttpCase, TransactionCase, new_test_user, tagged

NOM_INTERNE = "Revue interne du dossier fiscal 2026"


class _Mandat:

    @classmethod
    def _mandat(cls, env):
        """Un mandat de notaire fictif : 4 colonnes, dont une interne."""
        Colonne = env["project.task.type"]
        client = env["res.partner"].create({"name": "Famille Essai"})
        projet = env["project.project"].create({
            "name": "Vente 123 rue des Érables", "partner_id": client.id})
        colonnes = Colonne.create([
            {"name": "Ouverture", "sequence": 1, "progression_step_number": 1,
             "progression_step_name": "Ouverture du dossier",
             "progression_client_action_hint": "Signez la lettre de mandat."},
            {"name": "Pièces", "sequence": 2, "progression_step_number": 2,
             "progression_step_name": "Pièces du client",
             "progression_client_action_hint": "Déposez votre certificat de localisation."},
            {"name": "Revue", "sequence": 3, "progression_step_number": 3,
             "progression_client_visible": False,
             "progression_client_action_hint": "CONSIGNE INTERNE"},
            {"name": "Signature", "sequence": 4, "progression_step_number": 4,
             "progression_step_name": "Signature de l'acte"},
        ])
        projet.type_ids = colonnes
        Tache = env["project.task"]
        Tache.create({"name": "Ouvrir le dossier", "project_id": projet.id,
                      "stage_id": colonnes[0].id, "state": "1_done"})
        Tache.create({"name": "Recevoir le certificat", "project_id": projet.id,
                      "stage_id": colonnes[1].id, "state": "1_done"})
        Tache.create({"name": NOM_INTERNE, "project_id": projet.id,
                      "stage_id": colonnes[1].id})
        Tache.create({"name": "Préparer l'acte", "project_id": projet.id,
                      "stage_id": colonnes[3].id})
        return projet, colonnes


@tagged("post_install", "-at_install")
class TestEtapesDuPortail(TransactionCase, _Mandat):

    def test_etape_en_cours_et_consigne(self):
        projet, _colonnes = self._mandat(self.env)
        etapes = projet._progression_portal_steps()
        self.assertEqual([e["name"] for e in etapes],
                         ["Ouverture du dossier", "Pièces du client", "Signature de l'acte"])
        self.assertEqual([e["state"] for e in etapes], ["done", "current", "upcoming"])
        self.assertEqual(etapes[1]["percent"], 50)
        self.assertEqual(etapes[1]["hint"], "Déposez votre certificat de localisation.")
        # La consigne ne se montre qu'à l'étape en cours, et jamais celle d'une
        # colonne interne.
        self.assertEqual(etapes[0]["hint"], "")
        self.assertEqual(etapes[2]["hint"], "")
        self.assertNotIn("CONSIGNE INTERNE", str(etapes))

    def test_rien_commence_puis_tout_fini(self):
        projet, colonnes = self._mandat(self.env)
        # ⚠️ `task_ids` ne rend que les tâches ouvertes : on cherche toutes celles du projet.
        self.env["project.task"].search([("project_id", "=", projet.id)]).unlink()
        self.assertEqual({e["state"] for e in projet._progression_portal_steps()}, {"upcoming"})
        self.env["project.task"].create({"name": "Fait", "project_id": projet.id,
                                         "stage_id": colonnes[3].id, "state": "1_done"})
        self.assertEqual({e["state"] for e in projet._progression_portal_steps()}, {"done"})

    def test_sans_colonne_visible_rien_a_montrer(self):
        projet, colonnes = self._mandat(self.env)
        colonnes.progression_client_visible = False
        self.assertEqual(projet._progression_portal_steps(), [])


@tagged("post_install", "-at_install")
class TestGardeDePublication(TransactionCase, _Mandat):

    def test_seul_un_administrateur_publie(self):
        projet, _colonnes = self._mandat(self.env)
        usager = new_test_user(self.env, "progression_usager", groups="project.group_project_user")
        chef = new_test_user(self.env, "progression_chef", groups="project.group_project_manager")
        projet.privacy_visibility = "employees"
        vu = projet.with_user(usager)
        for geste in (vu.action_progression_portal_publish,
                      vu.action_progression_portal_copy_link,
                      vu.action_progression_portal_regenerate_token,
                      vu.action_progression_portal_unpublish,
                      lambda: vu.write({"progression_portal_published": True})):
            with self.assertRaises(AccessError):
                geste()
        # Le jeton n'est pas lisible hors du groupe.
        with self.assertRaises(AccessError):
            vu.read(["progression_portal_token"])

        projet.with_user(chef).action_progression_portal_publish()
        self.assertTrue(projet.progression_portal_published)
        self.assertTrue(projet.progression_portal_token)
        self.assertNotEqual(projet.progression_portal_token, projet.access_token)
        lien = projet.with_user(chef).action_progression_portal_copy_link()["params"]["message"]
        self.assertTrue(lien.endswith("/progression/%s/%s" % (projet.id, projet.progression_portal_token)))

    def test_un_jeton_ecrit_par_rpc_doit_etre_long_et_ascii(self):
        """Le champ est en lecture seule dans la vue, pas par RPC."""
        projet, _colonnes = self._mandat(self.env)
        chef = new_test_user(self.env, "progression_chef_jeton", groups="project.group_project_manager")
        vu = projet.with_user(chef)
        for jeton in ("1234", "a" * 31, "é" * 40, "a b" * 15, "x" * 129, "\t" * 40):
            with self.subTest(repr(jeton)[:20]), self.assertRaises(ValidationError):
                vu.write({"progression_portal_token": jeton})
        vu.write({"progression_portal_token": "A-b_c.~!" * 4})
        self.assertEqual(projet.progression_portal_token, "A-b_c.~!" * 4)
        # Vide : le portail ne s'ouvre à personne, et le jeton se refait à la publication.
        vu.write({"progression_portal_token": False})
        vu.action_progression_portal_publish()
        self.assertEqual(len(projet.progression_portal_token), 43)

    def test_la_copie_d_un_projet_ne_garde_ni_jeton_ni_publication(self):
        projet, _colonnes = self._mandat(self.env)
        projet.action_progression_portal_publish()
        copie = projet.copy()
        self.assertFalse(copie.progression_portal_published)
        self.assertFalse(copie.progression_portal_token)


@tagged("post_install", "-at_install")
class TestRouteDuPortail(HttpCase, _Mandat):

    def setUp(self):
        super().setUp()
        self.projet, _colonnes = self._mandat(self.env)
        self.projet.action_progression_portal_publish()
        self.jeton = self.projet.progression_portal_token

    def _ouvrir(self, project_id, jeton):
        return self.url_open("/progression/%s/%s" % (project_id, jeton), allow_redirects=False)

    def test_la_page_montre_les_etapes_sans_le_dossier_interne(self):
        reponse = self._ouvrir(self.projet.id, self.jeton)
        self.assertEqual(reponse.status_code, 200)
        page = reponse.text
        self.assertIn("Vente 123 rue des", page)
        self.assertIn("Pièces du client", page)
        self.assertIn("Déposez votre certificat de localisation.", page)
        self.assertNotIn(NOM_INTERNE, page)
        self.assertNotIn("CONSIGNE INTERNE", page)
        self.assertNotIn("Signez la lettre de mandat.", page)
        self.assertNotIn("Revue", page)
        self.assertEqual(reponse.headers.get("Referrer-Policy"), "no-referrer")
        self.assertIn("noindex", reponse.headers.get("X-Robots-Tag", ""))

    def test_tous_les_refus_repondent_404(self):
        autre, _c = self._mandat(self.env)
        autre.action_progression_portal_publish()
        cas = {
            "jeton faux": (self.projet.id, "x" * 43),
            "jeton d'un autre projet": (self.projet.id, autre.progression_portal_token),
            "projet absent": (999999999, self.jeton),
            # consteq lève TypeError sur du non ASCII : un 500, pas un 404.
            "jeton non ASCII": (self.projet.id, "%C3%A9"),
            "jeton trop long": (self.projet.id, "x" * 4000),
        }
        for nom, (pid, jeton) in cas.items():
            with self.subTest(nom):
                self.assertEqual(self._ouvrir(pid, jeton).status_code, 404)

        self.projet.action_progression_portal_unpublish()
        self.assertEqual(self._ouvrir(self.projet.id, self.jeton).status_code, 404)

    def test_une_nouvelle_adresse_ferme_l_ancienne(self):
        self.projet.action_progression_portal_regenerate_token()
        self.assertEqual(self._ouvrir(self.projet.id, self.jeton).status_code, 404)
        self.assertEqual(
            self._ouvrir(self.projet.id, self.projet.progression_portal_token).status_code, 200)

    def test_le_jeton_du_portail_n_ouvre_pas_le_projet(self):
        """Le jeton qu'on donne au client n'ouvre pas les routes standard du projet."""
        reponse = self.url_open(
            "/my/projects/%s?access_token=%s" % (self.projet.id, self.jeton),
            allow_redirects=False)
        self.assertNotEqual(reponse.status_code, 200)
        tache = self.projet.task_ids.filtered(lambda t: t.name == NOM_INTERNE)
        reponse = self.url_open(
            "/my/projects/%s/task/%s?access_token=%s" % (self.projet.id, tache.id, self.jeton),
            allow_redirects=False)
        self.assertNotEqual(reponse.status_code, 200)

    def test_la_page_parle_la_langue_du_client(self):
        """Le client du mandat lit la page dans sa langue, pas celle du navigateur."""
        self.env["res.lang"]._activate_lang("fr_CA")
        self.env["ir.module.module"]._load_module_terms(["bf_stepbystep_clients"], ["fr_CA"])
        self.projet.partner_id.lang = "fr_CA"
        page = self._ouvrir(self.projet.id, self.jeton).text
        self.assertIn("Ce que nous attendons de vous", page)
        self.assertIn("Étape", page)
        self.projet.partner_id.lang = "en_US"
        page = self._ouvrir(self.projet.id, self.jeton).text
        self.assertIn("What we need from you", page)
