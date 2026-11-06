from odoo.tests import Form, TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestSousTachesVisibles(TransactionCase):
    """Ce que le module promet, éprouvé sur les chemins du cœur d'Odoo 18."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.projet = cls.env["project.project"].create({"name": "Projet du banc"})
        cls.autre_projet = cls.env["project.project"].create({"name": "Autre projet"})
        cls.parent = cls.env["project.task"].create({
            "name": "Tâche mère",
            "project_id": cls.projet.id,
        })

    def _sous_tache_comme_la_vue(self, nom="Sous-tâche", projet=None, parent=None):
        """Crée une sous-tâche par le chemin de la vue du cœur, qui passe
        `default_display_in_project: False` dans son contexte."""
        return self.env["project.task"].with_context(
            default_display_in_project=False,
            default_parent_id=(parent or self.parent).id,
            default_project_id=(projet or self.projet).id,
        ).create({
            "name": nom,
            "parent_id": (parent or self.parent).id,
            "project_id": (projet or self.projet).id,
        })

    # --- Le drapeau -------------------------------------------------------

    def test_sous_tache_de_la_vue_reste_visible(self):
        sous = self._sous_tache_comme_la_vue()
        self.assertTrue(sous.display_in_project,
                        "le contexte de la vue du cœur ne doit plus cacher la sous-tâche")

    def test_defaut_du_contexte_ecarte(self):
        # Un défaut de contexte entre dans les valeurs de création et l'emporte
        # sur le calcul : s'il survit, l'œil disparaît en laissant les
        # sous-tâches cachées — le pire des deux mondes.
        valeurs = self.env["project.task"].with_context(
            default_display_in_project=False).default_get(["name", "display_in_project"])
        self.assertNotIn("display_in_project", valeurs)
        sous = self.env["project.task"].with_context(
            default_display_in_project=False,
            default_parent_id=self.parent.id).create({
                "name": "Sous-tâche par le défaut seul",
                "parent_id": self.parent.id,
                "project_id": self.projet.id,
            })
        self.assertTrue(sous.display_in_project)

    def test_ecriture_a_faux_forcee_a_vrai(self):
        sous = self._sous_tache_comme_la_vue()
        sous.write({"display_in_project": False})
        self.assertTrue(sous.display_in_project,
                        "écrire faux (import, XML-RPC, copie) ne doit plus cacher")

    def test_tache_sans_projet_ni_parent_ne_se_cache_pas(self):
        # Le seul chemin où le calcul ne repasse pas après la création : ni
        # projet ni parent dans les valeurs, donc aucune dépendance ne change.
        # C'est `create` qui écarte la clé.
        tache = self.env["project.task"].create({
            "name": "Tâche privée qu'on voudrait cacher",
            "display_in_project": False,
        })
        self.assertTrue(tache.display_in_project)

    def test_valeurs_fausses_non_booleennes_ecartees(self):
        # `0`, `None` et `""` ne sont pas `False` au sens de `is`, mais le cœur
        # les range à faux : une fiche serait restée cachée sans œil pour la
        # ressortir.
        sous = self._sous_tache_comme_la_vue()
        for valeur in (0, None, ""):
            sous.write({"display_in_project": valeur})
            sous.invalidate_recordset(["display_in_project"])
            self.assertTrue(sous.display_in_project,
                            "écrire %r ne doit pas cacher la sous-tâche" % (valeur,))
        for valeur in (0, None, ""):
            neuve = self.env["project.task"].create({
                "name": "Sous-tâche %r" % (valeur,),
                "parent_id": self.parent.id,
                "project_id": self.projet.id,
                "display_in_project": valeur,
            })
            self.assertTrue(neuve.display_in_project,
                            "créer avec %r ne doit pas cacher la sous-tâche" % (valeur,))

    def test_oeil_jamais_offert(self):
        sous = self._sous_tache_comme_la_vue()
        self.assertFalse(sous.show_display_in_project,
                         "les deux boutons œil de la fiche tiennent à ce champ")
        self.assertFalse(self.parent.show_display_in_project)

    def test_copie_de_projet_ne_cache_rien(self):
        self._sous_tache_comme_la_vue()
        copie = self.projet.copy()
        sous_copiees = copie.task_ids.child_ids
        self.assertTrue(sous_copiees, "la copie doit porter la sous-tâche")
        self.assertTrue(all(sous_copiees.mapped("display_in_project")),
                        "`project.project.copy` écrit faux sur les sous-tâches copiées")

    # --- Les vues ---------------------------------------------------------

    def test_sous_tache_dans_les_taches_du_projet(self):
        sous = self._sous_tache_comme_la_vue()
        action = self.env.ref("project.act_project_project_2_project_task_all")
        domaine = [("project_id", "=", self.projet.id), ("display_in_project", "=", True)]
        self.assertIn("display_in_project", action.domain,
                      "l'action du cœur porte toujours la feuille de domaine")
        self.assertIn(sous, self.env["project.task"].search(domaine),
                      "la sous-tâche doit paraître dans les tâches du projet")

    def test_compteur_du_projet_compte_les_sous_taches(self):
        avant = self.projet.task_count
        self._sous_tache_comme_la_vue()
        self.projet.invalidate_recordset(["task_count"])
        self.assertEqual(self.projet.task_count, avant + 1,
                         "le compteur de la carte de projet lit la même feuille de domaine")

    def test_filtre_sans_sous_taches_offert_et_eteint(self):
        # Les quatre vues primaires qui dérivent du socle, la vue de recherche
        # du PARTAGE DE PROJET comprise — celle que voit le client.
        for reference in ("project.view_task_search_form",
                          "project.view_task_search_form_project_base",
                          "project.view_task_search_form_project_fsm_base",
                          "project.project_sharing_project_task_view_search"):
            arch = self.env.ref(reference).get_combined_arch()
            self.assertIn('name="without_subtasks"', arch,
                          "le filtre doit descendre du socle jusqu'à %s" % reference)
        action = self.env.ref("project.act_project_project_2_project_task_all")
        self.assertNotIn("search_default_without_subtasks", action.context or "",
                         "le filtre est offert, jamais allumé d'office")

    # --- Le projet d'une sous-tâche ---------------------------------------

    def test_sous_tache_sans_projet_prend_celui_du_parent(self):
        sous = self.env["project.task"].create({
            "name": "Sous-tâche sans projet",
            "parent_id": self.parent.id,
        })
        self.assertEqual(sous.project_id, self.projet,
                         "sans projet, une sous-tâche prend celui de son parent")

    def test_formulaire_de_sous_tache_remplit_le_projet(self):
        # Le chemin réel : la fiche s'ouvre avec `default_parent_id` (bouton
        # « Sous-tâches » du parent), sans projet au contexte. Dans la fiche du
        # cœur, `parent_id` est invisible : personne ne le choisit à l'écran.
        formulaire = Form(self.env["project.task"].with_context(
            default_parent_id=self.parent.id))
        formulaire.name = "Sous-tâche à l'écran"
        self.assertEqual(formulaire.project_id, self.projet,
                         "le champ Projet doit se remplir avant l'enregistrement")
        sous = formulaire.save()
        self.assertTrue(sous.display_in_project)
        self.assertFalse(sous.show_display_in_project)

    def test_parent_deplace_emporte_les_enfants_du_meme_projet(self):
        suit = self._sous_tache_comme_la_vue("Suit son parent")
        reste = self._sous_tache_comme_la_vue("Rangée ailleurs", projet=self.autre_projet)
        troisieme = self.env["project.project"].create({"name": "Troisième projet"})

        self.parent.write({"project_id": troisieme.id})

        self.assertEqual(suit.project_id, troisieme,
                         "l'enfant resté dans l'ancien projet du parent le suit")
        self.assertEqual(reste.project_id, self.autre_projet,
                         "l'enfant rangé ailleurs ne bouge pas")

    def test_petits_enfants_suivent_aussi(self):
        sous = self._sous_tache_comme_la_vue("Sous-tâche")
        petit = self._sous_tache_comme_la_vue("Sous-sous-tâche", parent=sous)
        troisieme = self.env["project.project"].create({"name": "Troisième projet"})

        self.parent.write({"project_id": troisieme.id})

        self.assertEqual(sous.project_id, troisieme)
        self.assertEqual(petit.project_id, troisieme,
                         "le déplacement descend l'arbre entier")

    def test_reparentage_ne_deplace_pas_la_sous_tache(self):
        # Troisième règle du cœur que le drapeau portait : une sous-tâche CACHÉE
        # suivait son nouveau parent dans un autre projet. Décision de la
        # session : elle reste où on l'a rangée, et elle s'y voit. Essai posé
        # pour que ce soit une décision et non un hasard.
        sous = self._sous_tache_comme_la_vue("Sous-tâche reparentée")
        ailleurs = self.env["project.task"].create({
            "name": "Parent d'un autre projet",
            "project_id": self.autre_projet.id,
        })
        sous.write({"parent_id": ailleurs.id})
        self.assertEqual(sous.project_id, self.projet,
                         "le reparentage ne déménage pas la sous-tâche")
        self.assertTrue(sous.display_in_project,
                        "et elle reste visible dans le projet où elle est rangée")

    # --- L'archivage ------------------------------------------------------

    def test_archiver_le_parent_laisse_les_sous_taches_actives(self):
        sous = self._sous_tache_comme_la_vue()
        self.parent.action_archive()
        self.assertFalse(self.parent.active)
        self.assertTrue(sous.active,
                        "la cascade d'archivage du cœur ne visait que les sous-tâches cachées")
        self.assertTrue(sous.display_in_project)
