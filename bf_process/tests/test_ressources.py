# -*- coding: utf-8 -*-
"""Ressources accrochées aux étapes, et traçabilité vers la matrice."""
import base64

from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged

from .test_aller_retour import CARTE


@tagged("post_install", "-at_install")
class TestRessources(TransactionCase):

    def setUp(self):
        super().setUp()
        self.processus = self.env["bf.process"].create({
            "name": "Essai ressources", "code": "essai",
            "pool_name": "Blue Fox"})
        self.processus._charger_niveaux(CARTE)
        self.noeuds = {n.code: n for n in self.processus.diagram_ids.node_ids}
        self.piece = self.env["ir.attachment"].create({
            "name": "procedure.pdf", "datas": base64.b64encode(b"%PDF-1.4"),
            "mimetype": "application/pdf"})

    def _ressource(self, noeud, **vals):
        return self.env["bf.process.node.resource"].create(dict({
            "node_id": noeud.id, "name": "Comment faire",
            "attachment_id": self.piece.id}, **vals))

    # ------------------------------------------------------------- la cible
    def test_une_ressource_sans_cible_est_refusee(self):
        """Le pire cas d'atelier : un QR imprimé qui n'ouvre rien."""
        with self.assertRaises(ValidationError):
            self.env["bf.process.node.resource"].create({
                "node_id": self.noeuds["t1"].id, "name": "Nulle part"})

    def test_deux_cibles_sont_refusees(self):
        with self.assertRaises(ValidationError):
            self._ressource(self.noeuds["t1"], url="https://exemple.test/x")

    def test_la_cible_suit_le_genre_de_pointeur(self):
        r = self._ressource(self.noeuds["t1"])
        self.assertEqual(r.cible, "/web/content/%s?download=false" % self.piece.id)
        externe = self.env["bf.process.node.resource"].create({
            "node_id": self.noeuds["t1"].id, "name": "Fiche du fournisseur",
            "kind": "fiche", "url": "https://exemple.test/sds.pdf"})
        self.assertEqual(externe.cible, "https://exemple.test/sds.pdf")

    # -------------------------------------------------------------- le genre
    def test_seuls_certains_genres_sont_critiques(self):
        """Sécurité et geste à reproduire passent en tête à l'impression."""
        fiche = self._ressource(self.noeuds["t1"], kind="fiche")
        gabarit = self._ressource(self.noeuds["t1"], kind="gabarit",
                                  name="Le gabarit")
        self.assertTrue(fiche.critique)
        self.assertFalse(gabarit.critique)

    def test_une_annotation_ne_porte_pas_de_consigne(self):
        note = self.processus.diagram_ids.node_ids.filtered(
            lambda n: n.kind == "note")
        if not note:
            self.skipTest("la carte d'essai n'a pas d'annotation")
        with self.assertRaises(ValidationError):
            self._ressource(note[:1])

    # --------------------------------------------------------------- le gel
    def test_le_gel_couvre_les_ressources(self):
        """Une carte citée ne voit pas ses consignes changer sous elle."""
        r = self._ressource(self.noeuds["t1"])
        self.processus.action_valider()
        with self.assertRaises(UserError):
            r.write({"name": "Autre chose"})
        with self.assertRaises(UserError):
            self._ressource(self.noeuds["t1"], name="Une deuxième")
        with self.assertRaises(UserError):
            r.unlink()
        self.processus.action_rouvrir()
        r.write({"name": "Autre chose"})
        self.assertEqual(r.name, "Autre chose")

    # ------------------------------------------------------------- comptage
    def test_le_noeud_compte_ses_ressources(self):
        t1 = self.noeuds["t1"]
        self._ressource(t1, kind="fiche")
        self._ressource(t1, kind="gabarit", name="Le gabarit")
        t1.invalidate_recordset()
        self.assertEqual(t1.resource_count, 2)
        self.assertEqual(t1.resource_critique_count, 1)


@tagged("post_install", "-at_install")
class TestTracabilite(TransactionCase):

    def setUp(self):
        super().setUp()
        self.processus = self.env["bf.process"].create({
            "name": "Essai traçabilité", "code": "essai",
            "pool_name": "Blue Fox"})
        self.processus._charger_niveaux(CARTE)
        self.noeuds = {n.code: n for n in self.processus.diagram_ids.node_ids}
        self.projet = self.env["project.project"].create({"name": "Essai carto"})
        self.matrice = self.env["project.knowledge.matrix"].create({
            "name": "Matrice d'essai", "project_id": self.projet.id})
        self.section = self.env["project.knowledge.section"].create({
            "name": "Cartographie", "code": "S01"})
        self._compteur = 0

    def _element(self, nom="Pourquoi on revérifie les listes"):
        self._compteur += 1
        return self.env["project.knowledge.item"].create({
            "name": nom, "matrix_id": self.matrice.id,
            "section_id": self.section.id,
            "decision_id": "D%02d" % self._compteur,
            "project_id": self.projet.id})

    def test_une_etape_sait_ce_qui_l_explique(self):
        t1 = self.noeuds["t1"]
        self.assertFalse(t1.tracee)
        t1.knowledge_item_ids = [(4, self._element().id)]
        t1.invalidate_recordset()
        self.assertTrue(t1.tracee)
        self.assertEqual(t1.knowledge_item_count, 1)

    def test_les_rencontres_se_deduisent_des_elements(self):
        """On ne rattache pas une étape à une rencontre à la main."""
        Item = self.env["project.knowledge.item"]
        if "meeting_ids" not in Item._fields:
            self.skipTest("bf_meeting n'est pas installé")
        element = self._element()
        rencontre = self.env["meeting.record"].create({
            "name": "Séance de cartographie", "date": "2026-08-17 20:00:00"})
        element.meeting_ids = [(4, rencontre.id)]
        t1 = self.noeuds["t1"]
        t1.knowledge_item_ids = [(4, element.id)]
        t1.invalidate_recordset()
        self.assertEqual(t1.meeting_count, 1)
        self.assertEqual(t1._rencontres_ids(), [rencontre.id])

    def test_aucun_champ_typé_sur_le_modèle_des_rencontres(self):
        """⚠️ La régression qui a cassé l'installation neuve.

        Un Many2many vers `meeting.record`, même calculé et même gardé, rend
        `bf_meeting` obligatoire : Odoo résout le comodèle au chargement du
        registre. Le module doit donc n'avoir AUCUN champ typé sur ce modèle.
        Invisible sur un locataire où bf_meeting est installé, fatal ailleurs.
        """
        typés = [nom for nom, champ in self.env["bf.process.node"]._fields.items()
                 if getattr(champ, "comodel_name", None) == "meeting.record"]
        self.assertFalse(
            typés, "champ(s) typé(s) sur meeting.record : %s" % typés)

    def test_le_taux_de_tracabilite_ne_compte_que_les_activites(self):
        activites = self.processus.diagram_ids.node_ids.filtered(
            lambda n: n.kind in ("task", "send", "receive", "user", "sub"))
        self.assertEqual(self.processus.taux_tracabilite, 0.0)
        element = self._element()
        activites.write({"knowledge_item_ids": [(4, element.id)]})
        self.processus.invalidate_recordset()
        self.assertEqual(self.processus.taux_tracabilite, 100.0)
        self.assertEqual(self.processus.activite_tracee_count, len(activites))

    def test_le_rendu_annonce_les_pastilles(self):
        """Le tracé doit savoir quelles cases ouvrent quelque chose."""
        t1 = self.noeuds["t1"]
        t1.knowledge_item_ids = [(4, self._element().id)]
        self.env["bf.process.node.resource"].create({
            "node_id": t1.id, "name": "Comment faire",
            "url": "https://exemple.test/p.pdf"})
        rendu = self.processus.rendu()
        case = next(n for n in rendu["noeuds"] if n["id"] == "t1")
        self.assertEqual(case["ressources"], 1)
        self.assertEqual(case["traces"], 1)
        autre = next(n for n in rendu["noeuds"] if n["id"] != "t1")
        self.assertEqual(autre["ressources"], 0)
