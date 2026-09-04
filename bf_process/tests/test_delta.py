# -*- coding: utf-8 -*-
"""La branche du processus souhaité, les écarts, et les teintes.

Ce qui est éprouvé ici n'est pas « le diff trouve les différences » : ça, la
comparaison le faisait déjà. C'est ce que le semis fait de ce qu'une personne
a écrit par-dessus. Un semis qui recrée les écarts au lieu de les reconnaître
passe tous les tests de comptage et perd le travail en silence.
"""
from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged
from psycopg2 import IntegrityError
from odoo.tools import mute_logger

from .test_aller_retour import CARTE


@tagged("post_install", "-at_install")
class TestDelta(TransactionCase):

    def setUp(self):
        super().setUp()
        self.actuel = self.env["bf.process"].create({
            "name": "Traiter une demande", "code": "dem",
            "pool_name": "Blue Fox"})
        self.actuel._charger_niveaux(CARTE)
        self.noeuds = {n.code: n for n in self.actuel.diagram_ids.node_ids}

    def _cible(self):
        return self.env["bf.process"].browse(
            self.actuel.action_dessiner_cible()["res_id"])

    # --------------------------------------------------------------- branche
    def test_dessiner_la_cible_reprend_la_carte_sans_toucher_a_l_actuel(self):
        cible = self._cible()
        self.assertEqual(cible.nature, "cible")
        self.assertEqual(cible.origine_id, self.actuel)
        self.assertEqual(cible.version, "1.0")
        self.assertEqual(cible.state, "brouillon")
        self.assertEqual(cible.node_count, self.actuel.node_count)
        # mêmes codes des deux côtés : c'est ce qui rend le delta lisible
        self.assertEqual(sorted(cible.diagram_ids.node_ids.mapped("code")),
                         sorted(self.actuel.diagram_ids.node_ids.mapped("code")))
        # l'état actuel n'a pas bougé
        self.assertEqual(self.actuel.nature, "actuel")
        self.assertFalse(self.actuel.origine_id)
        self.assertEqual(self.actuel.cible_count, 1)

    def test_la_cible_ne_reprend_ni_la_validation_ni_la_prose(self):
        self.actuel.diagram_ids.node_ids.write({"valide_proprietaire": True})
        self.env["bf.process.section"].create({
            "process_id": self.actuel.id, "kind": "constat",
            "name": "Un seul couloir", "body": "<p>Tout repose sur une personne.</p>"})
        cible = self._cible()
        self.assertFalse(any(cible.diagram_ids.node_ids.mapped("valide_proprietaire")))
        self.assertFalse(cible.section_ids)

    def test_les_deux_branches_ont_chacune_leur_suite_de_versions(self):
        """Le même nom et le même numéro coexistent de part et d'autre."""
        cible = self._cible()
        self.assertEqual(cible.version, self.actuel.version)
        self.assertEqual(cible.name, self.actuel.name)
        # et la version suivante d'une branche ignore l'autre
        suite = self.env["bf.process"].browse(
            self.actuel.action_nouvelle_version()["res_id"])
        self.assertEqual(suite.version, "1.1")
        suite_cible = self.env["bf.process"].browse(
            cible.action_nouvelle_version()["res_id"])
        self.assertEqual(suite_cible.version, "1.1")
        self.assertEqual(suite_cible.origine_id, self.actuel)

    def test_deuxieme_cible_prend_le_numero_suivant(self):
        self.assertEqual(self._cible().version, "1.0")
        self.assertEqual(self._cible().version, "2.0")

    @mute_logger("odoo.sql_db")
    def test_le_meme_numero_deux_fois_dans_la_meme_branche_est_refuse(self):
        with self.assertRaises(IntegrityError):
            with self.cr.savepoint():
                self.env["bf.process"].create({
                    "name": self.actuel.name, "version": self.actuel.version,
                    "pool_name": "Blue Fox"})

    def test_une_cible_sans_origine_est_refusee(self):
        with self.assertRaises(ValidationError):
            self.env["bf.process"].create({
                "name": "Sans photo", "nature": "cible", "pool_name": "Blue Fox"})

    def test_un_etat_actuel_avec_origine_est_refuse(self):
        with self.assertRaises(ValidationError):
            self.env["bf.process"].create({
                "name": "Confus", "nature": "actuel",
                "origine_id": self.actuel.id, "pool_name": "Blue Fox"})

    def test_le_nom_affiche_distingue_les_deux_branches(self):
        """🔴 C'était un `name_get`, qu'Odoo 18 n'appelle plus.

        Le nom affiché retombait sur le seul champ `name`, donc l'état actuel
        et le processus souhaité étaient rigoureusement indiscernables dans
        toute liste déroulante, et le numéro de version avait disparu sans
        que rien ne le dise.
        """
        cible = self._cible()
        self.assertEqual(self.actuel.display_name,
                         f"{self.actuel.name} v{self.actuel.version}")
        self.assertEqual(cible.display_name,
                         f"{cible.name} v{cible.version} (souhaité)")
        self.assertNotEqual(cible.display_name, self.actuel.display_name)

    def test_un_niveau_ne_s_affiche_pas_comme_modele_et_identifiant(self):
        """🔴 Un niveau s'affichait « bf.process.diagram,90 »."""
        niveau = self.actuel.diagram_ids[0]
        self.assertNotIn("bf.process.diagram,", niveau.display_name)
        self.assertIn(niveau.title, niveau.display_name)

    def test_une_cible_ne_se_dessine_pas_d_apres_une_cible(self):
        cible = self._cible()
        with self.assertRaises(UserError):
            cible.action_dessiner_cible()

    # ----------------------------------------------------------------- semis
    def test_semis_sans_ecart_quand_les_cartes_sont_identiques(self):
        cible = self._cible()
        cible.action_semer_ecarts()
        self.assertEqual(cible.ecart_count, 0)

    def test_semis_voit_ce_qui_change(self):
        cible = self._cible()
        par_code = {n.code: n for n in cible.diagram_ids.node_ids}
        par_code["t1"].name = "Qualifier automatiquement la demande"
        par_code["e2"].unlink()
        cible.action_semer_ecarts()
        genres = cible.ecart_ids.mapped("genre")
        self.assertIn("renommage", genres)
        self.assertIn("retrait", genres)
        renommage = cible.ecart_ids.filtered(lambda e: e.genre == "renommage")
        self.assertEqual(renommage.node_code, "t1")
        self.assertEqual(renommage.node_actuel_id, self.noeuds["t1"])
        self.assertEqual(renommage.node_cible_id, par_code["t1"])
        self.assertTrue(all(e.source == "seme" for e in cible.ecart_ids))

    def test_semer_deux_fois_ne_recree_rien_et_garde_le_travail(self):
        """Le cœur du sujet : la moitié humaine survit au deuxième semis."""
        cible = self._cible()
        par_code = {n.code: n for n in cible.diagram_ids.node_ids}
        par_code["t1"].name = "Qualifier automatiquement la demande"
        cible.action_semer_ecarts()
        ecart = cible.ecart_ids.filtered(lambda e: e.genre == "renommage")
        self.assertEqual(len(ecart), 1)
        ecart.write({"intention": "automatiser", "gain": "Deux heures par semaine",
                     "etat": "retenu", "effort": "moyen"})
        avant = ecart.id

        cible.action_semer_ecarts()
        self.assertEqual(len(cible.ecart_ids), 1)
        garde = cible.ecart_ids
        self.assertEqual(garde.id, avant)
        self.assertEqual(garde.intention, "automatiser")
        self.assertEqual(garde.gain, "Deux heures par semaine")
        self.assertEqual(garde.etat, "retenu")
        self.assertFalse(garde.caduc)

    def test_un_ecart_rafraichi_suit_le_nouveau_libelle(self):
        cible = self._cible()
        par_code = {n.code: n for n in cible.diagram_ids.node_ids}
        par_code["t1"].name = "Qualifier vite"
        cible.action_semer_ecarts()
        ecart = cible.ecart_ids.filtered(lambda e: e.genre == "renommage")
        ecart.intention = "simplifier"
        par_code["t1"].name = "Qualifier automatiquement"
        cible.action_semer_ecarts()
        self.assertEqual(len(cible.ecart_ids), 1)
        self.assertIn("Qualifier automatiquement", cible.ecart_ids.libelle)
        self.assertEqual(cible.ecart_ids.intention, "simplifier")

    def test_un_ecart_disparu_sans_travail_s_en_va(self):
        cible = self._cible()
        par_code = {n.code: n for n in cible.diagram_ids.node_ids}
        par_code["t1"].name = "Qualifier vite"
        cible.action_semer_ecarts()
        self.assertEqual(cible.ecart_count, 1)
        par_code["t1"].name = self.noeuds["t1"].name
        cible.action_semer_ecarts()
        self.assertEqual(cible.ecart_count, 0)

    def test_un_ecart_disparu_qui_portait_du_travail_reste_marque_caduc(self):
        cible = self._cible()
        par_code = {n.code: n for n in cible.diagram_ids.node_ids}
        par_code["t1"].name = "Qualifier vite"
        cible.action_semer_ecarts()
        cible.ecart_ids.write({"intention": "simplifier", "etat": "retenu"})
        par_code["t1"].name = self.noeuds["t1"].name
        cible.action_semer_ecarts()
        self.assertEqual(cible.ecart_count, 1)
        self.assertTrue(cible.ecart_ids.caduc)
        self.assertEqual(cible.ecart_ids.intention, "simplifier")

    def test_un_ecart_ajoute_a_la_main_n_est_jamais_touche(self):
        cible = self._cible()
        manuel = self.env["bf.process.ecart"].create({
            "cible_id": cible.id, "cle": "manuel:1", "genre": "ajout",
            "portee": "noeud", "libelle": "Former le commis à la nouvelle boîte",
            "intention": "outiller"})
        cible.action_semer_ecarts()
        self.assertTrue(manuel.exists())
        self.assertFalse(manuel.caduc)
        self.assertEqual(manuel.source, "manuel")

    def test_semer_sur_un_etat_actuel_est_refuse(self):
        with self.assertRaises(UserError):
            self.actuel.action_semer_ecarts()

    def test_les_ecarts_ne_gelent_pas_avec_la_version(self):
        """La carte est la pièce datée ; le plan vit après elle."""
        cible = self._cible()
        par_code = {n.code: n for n in cible.diagram_ids.node_ids}
        par_code["t1"].name = "Qualifier automatiquement"
        cible.action_semer_ecarts()
        cible.action_valider()
        self.assertEqual(cible.state, "valide")
        cible.ecart_ids.etat = "fait"
        self.assertEqual(cible.ecart_ids.etat, "fait")

    def test_taux_de_transformation(self):
        cible = self._cible()
        par_code = {n.code: n for n in cible.diagram_ids.node_ids}
        par_code["t1"].name = "Qualifier automatiquement"
        par_code["e2"].unlink()
        cible.action_semer_ecarts()
        self.assertEqual(cible.taux_transformation, 0.0)
        # rien d'arrêté : le taux ne raconte pas d'histoire
        self.assertEqual(cible.ecart_a_decider_count, cible.ecart_count)
        ecarts = cible.ecart_ids
        ecarts[0].etat = "retenu"
        ecarts[1].etat = "fait"
        cible.invalidate_recordset()
        self.assertEqual(cible.ecart_retenu_count, 1)
        self.assertEqual(cible.ecart_fait_count, 1)
        self.assertEqual(cible.taux_transformation, 50.0)

    # --------------------------------------------------------------- teintes
    def test_teintes_des_deux_cotes(self):
        cible = self._cible()
        par_code = {n.code: n for n in cible.diagram_ids.node_ids}
        par_code["t1"].name = "Qualifier automatiquement"
        par_code["e2"].unlink()
        cible.action_semer_ecarts()
        niveau = cible.diagram_ids[0].code

        avant = cible._teintes("avant")
        apres = cible._teintes("apres")
        # ce qui disparaît se montre sur la carte d'avant, et seulement là
        self.assertEqual(avant.get((niveau, "e2")), "rouge")
        self.assertNotIn((niveau, "e2"), apres)
        # ce qui change se montre des deux côtés
        self.assertEqual(avant.get((niveau, "t1")), "ambre")
        self.assertEqual(apres.get((niveau, "t1")), "ambre")

    def test_un_ecart_ecarte_ne_teint_rien(self):
        cible = self._cible()
        par_code = {n.code: n for n in cible.diagram_ids.node_ids}
        par_code["e2"].unlink()
        cible.action_semer_ecarts()
        retrait = cible.ecart_ids.filtered(lambda e: e.genre == "retrait")
        self.assertTrue(cible._teintes("avant"))
        retrait.etat = "ecarte"
        self.assertFalse(cible._teintes("avant"))

    def test_la_carte_actuelle_ne_se_teint_pas_quand_il_y_a_deux_cibles(self):
        """Choisir en silence entre deux plans ferait lire le mauvais."""
        cible = self._cible()
        par_code = {n.code: n for n in cible.diagram_ids.node_ids}
        par_code["e2"].unlink()
        cible.action_semer_ecarts()
        self.assertTrue(self.actuel.teintes_de_la_carte())
        self._cible()
        self.actuel.invalidate_recordset()
        self.assertFalse(self.actuel.teintes_de_la_carte())
        # la cible, elle, sait toujours de quoi elle parle
        self.assertIsInstance(cible.teintes_de_la_carte(), dict)

    def test_la_teinte_voyage_jusqu_au_dict_d_echange(self):
        cible = self._cible()
        par_code = {n.code: n for n in cible.diagram_ids.node_ids}
        par_code["t1"].name = "Qualifier automatiquement"
        cible.action_semer_ecarts()
        pages = cible.to_dicts(cible._teintes("apres"))
        teintes = {n["id"]: n.get("teinte") for n in pages[0]["nodes"]}
        self.assertEqual(teintes["t1"], "ambre")
        self.assertIsNone(teintes["s"])
        # sans teintes demandées, le dict est celui d'avant, au bit près
        self.assertNotIn("teinte", cible.to_dicts()[0]["nodes"][0])

    def test_la_teinte_arrive_au_visualiseur(self):
        cible = self._cible()
        par_code = {n.code: n for n in cible.diagram_ids.node_ids}
        par_code["t1"].name = "Qualifier automatiquement"
        cible.action_semer_ecarts()
        rendu = cible.diagram_ids[0].rendu()
        teintes = {n["id"]: n["teinte"] for n in rendu["noeuds"]}
        self.assertEqual(teintes["t1"], "ambre")
        self.assertEqual(teintes["s"], "")
        self.assertIn("Vue delta", rendu["delta"])

    def test_pas_de_legende_sans_teinte(self):
        cible = self._cible()
        cible.action_semer_ecarts()
        self.assertEqual(cible.diagram_ids[0].rendu()["delta"], "")

    # -------------------------------------------------------------- livrable
    def test_vue_delta_pdf(self):
        cible = self._cible()
        par_code = {n.code: n for n in cible.diagram_ids.node_ids}
        par_code["e2"].unlink()
        cible.action_semer_ecarts()
        pages = cible._pages_delta()
        self.assertEqual(len(pages),
                         len(self.actuel.diagram_ids) + len(cible.diagram_ids))
        self.assertTrue(pages[0]["level"].startswith("État actuel"))
        self.assertTrue(pages[-1]["level"].startswith("Processus souhaité"))
        octets = cible._pdf_delta_octets()
        self.assertTrue(octets.startswith(b"%PDF"))
        self.assertGreater(len(octets), 3000)

    def test_vue_delta_refusee_sur_un_etat_actuel(self):
        with self.assertRaises(UserError):
            self.actuel._pdf_delta_octets()

    def test_le_bandeau_du_livrable_dit_la_nature(self):
        cible = self._cible()
        self.assertIn("AS-IS", self.actuel._document_meta()["droite"])
        self.assertIn("TO-BE", cible._document_meta()["droite"])

    def test_le_plan_de_transformation_entre_dans_le_livrable(self):
        cible = self._cible()
        par_code = {n.code: n for n in cible.diagram_ids.node_ids}
        par_code["t1"].name = "Qualifier automatiquement"
        par_code["e2"].unlink()
        cible.action_semer_ecarts()
        cible.ecart_ids[0].write({"intention": "automatiser",
                                  "gain": "Deux heures par semaine",
                                  "etat": "retenu"})
        plan = cible._document_plan()
        self.assertEqual(len(plan), cible.ecart_count)
        self.assertTrue(any(l["intention"] == "Automatiser" for l in plan))
        # un écart mis de côté sort du plan : un plan dit ce qu'on va faire
        cible.ecart_ids[1].etat = "ecarte"
        self.assertEqual(len(cible._document_plan()), cible.ecart_count - 1)
        octets = cible._document_octets()
        self.assertTrue(octets.startswith(b"%PDF"))

    def test_un_etat_actuel_n_a_pas_de_plan(self):
        self.assertEqual(self.actuel._document_plan(), [])

    # ---------------------------------------------------------------- tâches
    def test_creer_la_tache_depuis_un_ecart(self):
        projet = self.env["project.project"].create({"name": "Transformation"})
        cible = self._cible()
        cible.project_id = projet
        par_code = {n.code: n for n in cible.diagram_ids.node_ids}
        par_code["t1"].name = "Qualifier automatiquement"
        cible.action_semer_ecarts()
        ecart = cible.ecart_ids
        ecart.write({"intention": "automatiser", "gain": "Deux heures"})
        ecart.action_creer_tache()
        self.assertTrue(ecart.task_id)
        self.assertEqual(ecart.task_id.project_id, projet)
        self.assertIn("Deux heures", ecart.task_id.description)
        with self.assertRaises(UserError):
            ecart.action_creer_tache()

    def test_creer_la_tache_sans_projet_est_refuse(self):
        cible = self._cible()
        par_code = {n.code: n for n in cible.diagram_ids.node_ids}
        par_code["t1"].name = "Qualifier automatiquement"
        cible.action_semer_ecarts()
        with self.assertRaises(UserError):
            cible.ecart_ids.action_creer_tache()
