"""Les libellés que le tableau de bord compose en Python passent par le catalogue.

Ils s'affichent tels quels dans le composant OWL : sans `_()`, un usager
francophone lirait l'anglais de la source.
"""
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestLangueDuTableauDeBord(TransactionCase):

    def test_le_titre_de_la_liste_des_taches(self):
        self.env["res.lang"]._activate_lang("fr_CA")
        self.env["res.lang"]._activate_lang("en_US")
        tableau = self.env["bf.stepbystep.dashboard"]
        projet = self.env["project.project"].create({"name": "Pas-à-pas"})
        self.assertEqual(
            tableau.with_context(lang="fr_CA").action_open_project(projet.id)["name"],
            "Tâches du projet")
        self.assertEqual(
            tableau.with_context(lang="en_US").action_open_project(projet.id)["name"],
            "Project tasks")

    def test_les_donnees_se_lisent_dans_la_langue_de_l_usager(self):
        """Les requêtes lisent d'abord la clé de la langue de l'usager.

        Un type d'activité porte ses deux langues : un usager anglophone lisait
        son nom français parce que la requête prenait fr_CA en premier.
        """
        self.env["res.lang"]._activate_lang("fr_CA")
        self.env["res.lang"]._activate_lang("en_US")
        tableau = self.env["bf.stepbystep.dashboard"]
        projet = self.env["project.project"].create({"name": "Pas-à-pas langue"})
        tache = self.env["project.task"].create({"name": "Étape", "project_id": projet.id})
        # Un type à soi, nommé dans les deux langues : ceux de la base copiée peuvent
        # porter le même nom partout (« To-Do » sur certaines bases).
        rappel = self.env["mail.activity.type"].with_context(lang="en_US").create({"name": "Call back"})
        rappel.update_field_translations("name", {"fr_CA": "Rappeler"})
        tache.activity_schedule(activity_type_id=rappel.id, date_deadline="2999-01-01")
        for lang, attendu in (("fr_CA", "Rappeler"), ("en_US", "Call back")):
            echeances = tableau.with_context(lang=lang)._get_next_deadline_by_project()
            self.assertEqual(echeances[projet.id]["label"], attendu)
            # Le reste du tableau s'exécute sans erreur dans les deux langues.
            tableau.with_context(lang=lang).get_dashboard_data()
            tableau.with_context(lang=lang).get_client_detail(projet.id)
