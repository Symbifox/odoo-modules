# -*- coding: utf-8 -*-
"""La source, contre une vraie base.

⚠️ Chaque enregistrement est créé ici, jamais emprunté aux données de démo :
un compte de banc vide cache exactement les valeurs qui cassent
(dates absentes, tâche sans responsable, jalon sans échéance).
"""
import base64
from datetime import date, datetime, timedelta

# Un PNG 1x1 valide, pour donner un logo à la société sans dépendre d'un fichier.
_PNG_MINIME = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")

from odoo.exceptions import AccessError, ValidationError
from odoo.tests.common import TransactionCase, tagged, new_test_user


@tagged("post_install", "-at_install", "bf_gantt")
class TestSource(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.source = cls.env["bf.gantt.source"]
        cls.projet = cls.env["project.project"].create({
            "name": "Banc échéancier",
            "allow_task_dependencies": True,
        })
        etapes = cls.env["project.task.type"].create([
            {"name": "À faire", "sequence": 1, "project_ids": [(4, cls.projet.id)]},
            {"name": "En cours", "sequence": 2, "project_ids": [(4, cls.projet.id)]},
        ])
        cls.etape_todo, cls.etape_wip = etapes
        cls.aujourdhui = date.today()

    def _tache(self, nom, **valeurs):
        base = {
            "name": nom,
            "project_id": self.projet.id,
            "stage_id": self.etape_todo.id,
        }
        base.update(valeurs)
        return self.env["project.task"].create(base)

    # ------------------------------------------------------------ les dates

    def test_le_debut_planifie_prime_sur_l_assignation(self):
        tache = self._tache(
            "Planifiée",
            planned_date_begin=datetime(2026, 9, 7, 9, 0),
            date_deadline=datetime(2026, 9, 18, 17, 0),
        )
        payload = self.source.get_echeancier("project", self.projet.id)
        barre = next(b for b in payload["tasks"] if b["id"] == tache.id)
        self.assertEqual(barre["start"], "2026-09-07")
        self.assertEqual(barre["start_origin"], "planifie")

    def test_sans_debut_planifie_on_retombe_et_on_le_dit(self):
        tache = self._tache("Sans début",
                            date_deadline=datetime(2026, 9, 18, 17, 0))
        tache.date_assign = datetime(2026, 9, 2, 8, 0)
        payload = self.source.get_echeancier("project", self.projet.id)
        barre = next(b for b in payload["tasks"] if b["id"] == tache.id)
        self.assertEqual(barre["start"], "2026-09-02")
        self.assertEqual(barre["start_origin"], "assignation")

    def test_une_tache_sans_aucune_date_reste_tracable(self):
        """Elle ne doit ni disparaître ni produire une barre absurde."""
        tache = self._tache("Nue")
        payload = self.source.get_echeancier("project", self.projet.id)
        barre = next(b for b in payload["tasks"] if b["id"] == tache.id)
        self.assertLessEqual(barre["start"], barre["end"])

    def test_l_origine_calculee_suit_les_champs(self):
        tache = self._tache("Origine")
        self.assertEqual(tache.bf_gantt_start_origin, "creation")
        tache.planned_date_begin = datetime(2026, 9, 7, 9, 0)
        self.assertEqual(tache.bf_gantt_start_origin, "planifie")

    def test_un_debut_apres_l_echeance_est_refuse(self):
        with self.assertRaises(ValidationError):
            self._tache("À l'envers",
                        planned_date_begin=datetime(2026, 9, 20, 9, 0),
                        date_deadline=datetime(2026, 9, 10, 17, 0))

    # --------------------------------------------------------- le regroupement

    def test_le_regroupement_par_defaut_est_l_etape(self):
        self._tache("A", stage_id=self.etape_todo.id,
                    date_deadline=datetime(2026, 9, 10, 17, 0))
        self._tache("B", stage_id=self.etape_wip.id,
                    date_deadline=datetime(2026, 9, 12, 17, 0))
        payload = self.source.get_echeancier("project", self.projet.id)
        cles = {c["key"] for c in payload["lanes"]}
        self.assertIn("stage-%s" % self.etape_todo.id, cles)
        self.assertIn("stage-%s" % self.etape_wip.id, cles)

    def test_le_regroupement_par_responsable_separe_le_sans_responsable(self):
        usager = new_test_user(self.env, login="banc_gantt_u1")
        self._tache("Assignée", user_ids=[(6, 0, usager.ids)],
                    date_deadline=datetime(2026, 9, 10, 17, 0))
        # ⚠️ `project.task.user_ids` a pour défaut l'usager courant : une tâche
        # créée sans rien n'est PAS orpheline, il faut vider explicitement.
        self._tache("Orpheline", user_ids=[(5, 0, 0)],
                    date_deadline=datetime(2026, 9, 11, 17, 0))
        payload = self.source.get_echeancier(
            "project", self.projet.id, grouping="assignee")
        cles = {c["key"] for c in payload["lanes"]}
        self.assertIn("assignee-none", cles)
        self.assertIn("assignee-%s" % usager.id, cles)

    def test_plusieurs_responsables_se_disent_en_un_nom_plus_un_compte(self):
        """⚠️ Deux noms abrégés collés dépassent la colonne et le nom de la tâche
        passe dessous. Le raccourci est fait à la source, pas à l'affichage."""
        a = new_test_user(self.env, login="banc_gantt_r1", name="Alice Nadeau")
        b = new_test_user(self.env, login="banc_gantt_r2", name="Bruno Lévesque")
        c = new_test_user(self.env, login="banc_gantt_r3", name="Chloé Ouimet")
        tache = self._tache("À plusieurs", user_ids=[(6, 0, (a + b + c).ids)],
                            date_deadline=datetime(2026, 9, 20, 17, 0))
        payload = self.source.get_echeancier("project", self.projet.id)
        barre = next(x for x in payload["tasks"] if x["id"] == tache.id)
        self.assertRegex(barre["assignee"], r"^\S+ \S\. \+2$")
        self.assertLess(len(barre["assignee"]), 24)

    def test_un_seul_responsable_garde_son_nom_abrege(self):
        u = new_test_user(self.env, login="banc_gantt_r4", name="Alice Nadeau")
        tache = self._tache("Seule", user_ids=[(6, 0, u.ids)],
                            date_deadline=datetime(2026, 9, 20, 17, 0))
        payload = self.source.get_echeancier("project", self.projet.id)
        barre = next(x for x in payload["tasks"] if x["id"] == tache.id)
        self.assertEqual(barre["assignee"], "Alice N.")

    def test_le_regroupement_aucun_range_tout_ensemble(self):
        self._tache("A", stage_id=self.etape_todo.id,
                    date_deadline=datetime(2026, 9, 10, 17, 0))
        self._tache("B", stage_id=self.etape_wip.id,
                    date_deadline=datetime(2026, 9, 12, 17, 0))
        payload = self.source.get_echeancier(
            "project", self.projet.id, grouping="none")
        self.assertEqual([c["key"] for c in payload["lanes"]], ["all"])

    def test_le_module_ne_depend_pas_des_etapes_de_progression(self):
        """`bf_stepbystep_clients` peut être absent : on l'offre, on n'en dépend pas."""
        offert = self.source._etapes_de_progression_disponibles()
        groupings = self.source.get_portefeuille()["groupings"]["project"]
        cles = [g["key"] for g in groupings]
        self.assertEqual("step" in cles, offert)

    # ----------------------------------------------------------- dépendances

    def test_les_dependances_deviennent_des_liens(self):
        amont = self._tache("Amont", date_deadline=datetime(2026, 9, 10, 17, 0))
        aval = self._tache("Aval", date_deadline=datetime(2026, 9, 20, 17, 0),
                           depend_on_ids=[(6, 0, amont.ids)])
        payload = self.source.get_echeancier("project", self.projet.id)
        self.assertIn({"from": "task-%s" % amont.id, "to": "task-%s" % aval.id},
                      payload["deps"])

    def test_une_dependance_hors_projet_est_ecartee(self):
        autre = self.env["project.project"].create({"name": "Ailleurs"})
        amont = self.env["project.task"].create({
            "name": "Amont ailleurs", "project_id": autre.id,
            "date_deadline": datetime(2026, 9, 10, 17, 0)})
        self._tache("Aval", date_deadline=datetime(2026, 9, 20, 17, 0),
                    depend_on_ids=[(6, 0, amont.ids)])
        payload = self.source.get_echeancier("project", self.projet.id)
        self.assertEqual(payload["deps"], [])

    # --------------------------------------------------------------- jalons

    def test_les_jalons_du_projet_apparaissent_en_losanges(self):
        self.projet.allow_milestones = True
        jalon = self.env["project.milestone"].create({
            "name": "Bascule", "project_id": self.projet.id,
            "deadline": date(2026, 10, 1)})
        self._tache("Une tâche", date_deadline=datetime(2026, 9, 20, 17, 0))
        payload = self.source.get_echeancier("project", self.projet.id)
        barre = next(b for b in payload["tasks"]
                     if b["ref"] == "milestone-%s" % jalon.id)
        self.assertTrue(barre["is_milestone"])
        self.assertEqual(barre["start"], barre["end"])

    # ------------------------------------------------------------- plan seul

    def test_un_plan_autonome_rend_le_meme_dictionnaire(self):
        plan = self.env["bf.gantt.plan"].create({
            "name": "Plan de devis",
            "item_ids": [
                (0, 0, {"name": "Cadrage", "lane": "Phase 1",
                        "date_start": date(2026, 9, 7),
                        "date_end": date(2026, 9, 18), "progress": 50}),
                (0, 0, {"name": "Signature", "lane": "Phase 1",
                        "date_start": date(2026, 9, 21), "is_milestone": True}),
            ],
        })
        payload = self.source.get_echeancier("plan", plan.id, grouping="lane")
        self.assertEqual(payload["source"]["kind"], "plan")
        self.assertEqual(len(payload["tasks"]), 2)
        self.assertEqual(set(payload.keys()),
                         set(self.source.get_echeancier(
                             "project", self.projet.id).keys()))

    def test_un_plan_ne_cree_aucune_tache(self):
        avant = self.env["project.task"].search_count([])
        self.env["bf.gantt.plan"].create({
            "name": "Sans tâches",
            "project_id": self.projet.id,
            "item_ids": [(0, 0, {"name": "Ligne", "date_start": date(2026, 9, 7)})],
        })
        self.assertEqual(self.env["project.task"].search_count([]), avant)

    def test_un_cycle_de_dependances_est_refuse(self):
        plan = self.env["bf.gantt.plan"].create({"name": "Cycle"})
        a = self.env["bf.gantt.item"].create({
            "plan_id": plan.id, "name": "A", "date_start": date(2026, 9, 7)})
        b = self.env["bf.gantt.item"].create({
            "plan_id": plan.id, "name": "B", "date_start": date(2026, 9, 8),
            "depend_on_ids": [(6, 0, a.ids)]})
        with self.assertRaises(ValidationError):
            a.depend_on_ids = [(6, 0, b.ids)]

    def test_un_avancement_hors_bornes_est_refuse(self):
        plan = self.env["bf.gantt.plan"].create({"name": "Bornes"})
        with self.assertRaises(ValidationError):
            self.env["bf.gantt.item"].create({
                "plan_id": plan.id, "name": "Trop", "progress": 140,
                "date_start": date(2026, 9, 7)})

    # ------------------------------------------------------ publier est un droit

    def test_publier_sans_le_groupe_est_refuse(self):
        """🔴 La garde ne vivait que dans la vue : le champ restait écrivable par
        RPC, et écrire sur `project.project` est un droit répandu."""
        chef = new_test_user(self.env, login="banc_gantt_chef_projet",
                             groups="project.group_project_manager")
        projet = self.projet.with_user(chef)
        with self.assertRaises(AccessError):
            projet.action_bf_gantt_publier()
        with self.assertRaises(AccessError):
            projet.write({"bf_gantt_published": True})

    def test_le_superusager_publie_sans_le_groupe(self):
        """⚠️ Une garde applicative qui refuse `env.su` casse les migrations, les
        fichiers de données et les actions serveur, sans rien protéger de plus."""
        self.projet.sudo().with_context(active_test=False).action_bf_gantt_publier()
        self.assertTrue(self.projet.bf_gantt_published)

    def test_publier_avec_le_groupe_passe(self):
        gestionnaire = new_test_user(
            self.env, login="banc_gantt_gestion",
            groups="bf_gantt.group_bf_gantt_manager,project.group_project_manager")
        self.projet.with_user(gestionnaire).action_bf_gantt_publier()
        self.assertTrue(self.projet.bf_gantt_published)

    def test_un_plan_ne_se_publie_pas_sans_le_groupe(self):
        plan = self.env["bf.gantt.plan"].create({"name": "À publier"})
        lecteur = new_test_user(self.env, login="banc_gantt_lecteur",
                                groups="bf_gantt.group_bf_gantt_user")
        with self.assertRaises(AccessError):
            plan.with_user(lecteur).action_bf_gantt_publier()

    def test_le_module_ne_garde_pas_un_champ_qui_n_est_pas_le_sien(self):
        """⚠️ `access_token` appartient à `portal.mixin` : le garder casserait
        l'envoi d'un projet par courriel pour qui n'a pas notre groupe."""
        chef = new_test_user(self.env, login="banc_gantt_chef2",
                             groups="project.group_project_manager")
        self.projet.with_user(chef).write({"access_token": "un-token-quelconque"})
        self.assertEqual(self.projet.access_token, "un-token-quelconque")

    # ----------------------------------------------------------- la géométrie

    def test_la_geometrie_est_serialisable_en_JSON(self):
        """🔴 C'est le contrôle qui manquait. `boite_logo` rend les OCTETS du
        fichier ; laissés dans la réponse, `json.dumps` meurt et le navigateur
        affiche « Connection … couldn't be established ». Éprouver la méthode en
        Python ne le voit pas : il faut sérialiser comme le fait le RPC."""
        import json

        self.env.company.logo = base64.b64encode(_PNG_MINIME)
        self._tache("Avec logo", date_deadline=datetime(2026, 9, 20, 17, 0))
        g = self.source.get_geometrie("project", self.projet.id)
        json.dumps(g)            # ne doit lever aucune exception
        self.assertIsNone(g.get("logo"))
        self.assertEqual(g["societe"]["logo"], "")

    def test_la_geometrie_rend_les_details_pour_l_infobulle(self):
        tache = self._tache("Détail", date_deadline=datetime(2026, 9, 20, 17, 0))
        g = self.source.get_geometrie("project", self.projet.id)
        self.assertIn("task-%s" % tache.id, g["details"])
        self.assertIn("lignes", g)

    # --------------------------------------------------------------- exports

    def test_les_cinq_formats_sortent_depuis_un_vrai_projet(self):
        self._tache("Une tâche", planned_date_begin=datetime(2026, 9, 7, 9, 0),
                    date_deadline=datetime(2026, 9, 20, 17, 0))
        export = self.env["bf.gantt.export"]
        payload = self.source.get_echeancier("project", self.projet.id)
        for format_ in ("pdf", "png", "svg", "xlsx", "mspdi"):
            contenu, mime, nom = export._rendre(payload, format_)
            self.assertTrue(contenu, format_)
            self.assertTrue(mime, format_)
            self.assertTrue(nom.startswith("banc-echeancier"), nom)

    def test_le_depot_en_piece_jointe_atteint_le_fil(self):
        """🔴 Une pièce posée par `res_id` seul reste invisible dans le chatter."""
        self._tache("Une tâche", date_deadline=datetime(2026, 9, 20, 17, 0))
        res = self.env["bf.gantt.export"].joindre(
            "project", self.projet.id, "pdf")
        message = self.env["mail.message"].search(
            [("model", "=", "project.project"), ("res_id", "=", self.projet.id)],
            order="id desc", limit=1)
        self.assertIn(res["attachment_id"], message.attachment_ids.ids)


@tagged("post_install", "-at_install", "bf_gantt")
class TestCopierLien(TransactionCase):
    """⚠️ `action_bf_gantt_copier_lien` est publique : appelable par RPC par
    quiconque lit le projet. Elle frappe le jeton et rend l'adresse privée, donc
    elle demande le droit de publier, pas seulement celui de lire."""

    def setUp(self):
        super().setUp()
        self.projet = self.env["project.project"].create({"name": "Banc lien"})

    def test_un_lecteur_ne_peut_pas_se_fabriquer_l_adresse(self):
        lecteur = new_test_user(
            self.env, login="banc_gantt_lecteur_lien",
            groups="project.group_project_user,bf_gantt.group_bf_gantt_user")
        with self.assertRaises(AccessError):
            self.projet.with_user(lecteur).action_bf_gantt_copier_lien()

    def test_le_droit_de_publier_ouvre_l_adresse(self):
        gestionnaire = new_test_user(
            self.env, login="banc_gantt_gestion_lien",
            groups="project.group_project_manager,bf_gantt.group_bf_gantt_manager")
        action = self.projet.with_user(gestionnaire).action_bf_gantt_copier_lien()
        self.assertIn("/mon/echeancier/project/", action["params"]["message"])
        self.assertTrue(self.projet.access_token)
