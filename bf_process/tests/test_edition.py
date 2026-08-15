# -*- coding: utf-8 -*-
"""Le tracé écrit dans les enregistrements, et refuse quand il le doit."""
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from .test_aller_retour import CARTE


@tagged("post_install", "-at_install")
class TestEdition(TransactionCase):

    def setUp(self):
        super().setUp()
        self.processus = self.env["bf.process"].create({
            "name": "Essai édition", "code": "edit", "pool_name": "Blue Fox"})
        self.processus._charger_niveaux(CARTE)
        self.niveau = self.processus.diagram_ids
        self.noeuds = {n.code: n for n in self.niveau.node_ids}

    def _centre(self, code):
        """Le centre d'un nœud, dans les coordonnées que le client reçoit."""
        n = [x for x in self.niveau.rendu()["noeuds"] if x["id"] == code][0]
        return n["x"] + n["w"] / 2, n["y"] + n["h"] / 2

    # --- déplacement ---------------------------------------------------------
    def test_deplacer_une_colonne_entiere(self):
        """Déplacer d'une largeur de colonne avance d'exactement une colonne."""
        cx, cy = self._centre("t1")
        avant = self.noeuds["t1"].col
        self.niveau.deplacer_noeud("t1", cx + self.niveau.col_w, cy)
        self.assertAlmostEqual(self.noeuds["t1"].col, avant + 1.0, places=4)

    def test_deplacement_cale_sur_la_grille(self):
        """Un déplacement quelconque ne laisse pas un flottant sale dans col.

        Sans l'arrondi, chaque glisser-déposer déposerait des décimales que
        personne n'a demandées, et les exports s'écarteraient du PDF.
        """
        cx, cy = self._centre("t1")
        self.niveau.deplacer_noeud("t1", cx + 37.427, cy)
        pas = self.niveau.pas_grille
        reste = round(self.noeuds["t1"].col / pas) * pas - self.noeuds["t1"].col
        self.assertAlmostEqual(reste, 0.0, places=6)

    def test_deplacement_change_de_couloir(self):
        """Lâché dans une autre bande, le nœud change de couloir."""
        couloirs = self.niveau.rendu()["couloirs"]
        technique = [c for c in couloirs if c["code"] == "t"][0]
        cx, _ = self._centre("s")
        self.assertEqual(self.noeuds["s"].lane_id.code, "f")
        self.niveau.deplacer_noeud("s", cx, technique["y"] + technique["h"] / 2)
        self.assertEqual(self.noeuds["s"].lane_id.code, "t")

    def test_deplacer_rend_le_trace_a_jour(self):
        """Chaque écriture renvoie le tracé complet : le client ne devine rien."""
        cx, cy = self._centre("t1")
        rendu = self.niveau.deplacer_noeud("t1", cx + self.niveau.col_w, cy)
        self.assertIn("noeuds", rendu)
        apres = [n for n in rendu["noeuds"] if n["id"] == "t1"][0]
        self.assertNotEqual(apres["x"] + apres["w"] / 2, cx)

    # --- gel ------------------------------------------------------------------
    def _utilisateur(self, groupe):
        return self.env["res.users"].create({
            "name": "Essai %s" % groupe,
            "login": "essai-edition-%s" % groupe,
            "email": "essai-%s@example.invalid" % groupe,
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id,
                                  self.env.ref("bf_process.%s" % groupe).id])]})

    def test_lecture_seule_ne_peut_pas_ecrire_depuis_le_trace(self):
        """L'autre moitié de la garde : le droit, pas seulement le gel.

        Le gel refuse tout le monde ; un contrôle fait sur une carte validée ne
        dit donc rien du contrôle d'accès. Celui-ci porte sur un BROUILLON,
        où seul le droit d'écriture peut refuser.
        """
        lecteur = self._utilisateur("group_bf_process_user")
        self.assertEqual(self.processus.state, "brouillon")
        niveau = self.niveau.with_user(lecteur)
        self.assertFalse(niveau._modifiable())
        for appel in (
            lambda: niveau.deplacer_noeud("t1", 300.0, 60.0),
            lambda: niveau.creer_noeud("task", 500.0, 60.0, "Intrus"),
            lambda: niveau.supprimer_noeud("t1"),
            lambda: niveau.creer_flux("s", "e"),
            lambda: niveau.supprimer_flux("s", "t1"),
        ):
            with self.assertRaises(UserError):
                appel()

    def test_gestion_peut_ecrire_depuis_le_trace(self):
        gestion = self._utilisateur("group_bf_process_manager")
        niveau = self.niveau.with_user(gestion)
        self.assertTrue(niveau._modifiable())
        avant = len(self.niveau.node_ids)
        niveau.creer_noeud("task", 700.0, 60.0, "Ajoutée")
        self.assertEqual(len(self.niveau.node_ids), avant + 1)

    def test_gel_ferme_l_editeur_avant_la_poignee(self):
        """Une version validée se dit non modifiable AVANT qu'on tire dessus."""
        self.processus.action_valider()
        rendu = self.niveau.rendu()
        self.assertFalse(rendu["modifiable"])
        self.assertTrue(rendu["gele"])
        cx, cy = self._centre("t1")
        with self.assertRaisesRegex(UserError, "version validée"):
            self.niveau.deplacer_noeud("t1", cx + 40, cy)
        with self.assertRaisesRegex(UserError, "version validée"):
            self.niveau.creer_noeud("task", cx, cy)

    def test_rouvrir_redonne_la_main(self):
        self.processus.action_valider()
        self.processus.action_rouvrir()
        self.assertTrue(self.niveau.rendu()["modifiable"])

    # --- création et suppression ---------------------------------------------
    def test_creer_noeud_code_et_identifiant(self):
        """Un nœud posé prend un code LIBRE, pas le premier venu.

        La carte d'essai porte déjà un nœud « n1 » : le code généré doit
        l'enjamber, sinon la contrainte d'unicité le refuse en aval sans dire
        pourquoi le code choisi ne convenait pas.
        """
        self.assertIn("n1", self.niveau.node_ids.mapped("code"))
        cx, cy = self._centre("t1")
        self.niveau.creer_noeud("user", cx, cy + 60)
        neuf = self.niveau.node_ids.filtered(lambda n: n.code == "n2")
        self.assertTrue(neuf, "le code n1 était pris, n2 devait être choisi")
        self.assertEqual(neuf.bpmn_id, f"{self.niveau.bpmn_id}_n2")
        self.assertEqual(neuf.kind, "user")
        self.assertTrue(neuf.name, "une activité sans nom serait refusée")
        self.niveau.creer_noeud("task", cx, cy + 120)
        self.assertTrue(self.niveau.node_ids.filtered(lambda n: n.code == "n3"))

    def test_creer_noeud_refuse_un_genre_inconnu(self):
        cx, cy = self._centre("t1")
        with self.assertRaisesRegex(UserError, "type de nœud"):
            self.niveau.creer_noeud("licorne", cx, cy)

    def test_sequences_denses_apres_suppression(self):
        """L'ordre des enregistrements EST l'ordre d'émission : pas de trou."""
        self.niveau.supprimer_noeud("e2")
        sequences = self.niveau.node_ids.sorted(
            lambda n: (n.sequence, n.id)).mapped("sequence")
        self.assertEqual(sequences, [10 * (i + 1) for i in range(len(sequences))])

    def test_supprimer_noeud_emporte_ses_flux(self):
        avant = len(self.niveau.flow_ids)
        self.niveau.supprimer_noeud("e2")
        self.assertFalse(self.niveau.node_ids.filtered(lambda n: n.code == "e2"))
        self.assertEqual(len(self.niveau.flow_ids), avant - 1)

    def test_supprimer_un_sous_processus_qui_ouvre_une_page_refuse(self):
        """Retirer l'appelant rendrait la page enfant inatteignable en silence."""
        carte = [
            {"title": "Vue d'ensemble", "pool": "Blue Fox",
             "nodes": [
                 {"id": "s", "kind": "start", "name": "Départ", "col": 0, "row": 0},
                 {"id": "a", "kind": "sub", "name": "Traiter", "col": 1, "row": 0}],
             "flows": [{"src": "s", "tgt": "a"}]},
            {"title": "Traiter", "pool": "Blue Fox",
             "nodes": [
                 {"id": "s", "kind": "start", "name": "Ouvert", "col": 0, "row": 0},
                 {"id": "e", "kind": "end", "name": "Traité", "col": 1, "row": 0}],
             "flows": [{"src": "s", "tgt": "e"}]},
        ]
        p = self.env["bf.process"].create({"name": "Avec enfant",
                                           "pool_name": "Blue Fox"})
        p._charger_niveaux(carte)
        with self.assertRaisesRegex(UserError, "inatteignable"):
            p.diagram_ids[0].supprimer_noeud("a")

    # --- liens ------------------------------------------------------------------
    def test_creer_et_couper_un_lien(self):
        avant = len(self.niveau.flow_ids)
        self.niveau.creer_flux("e2", "t1")
        self.assertEqual(len(self.niveau.flow_ids), avant + 1)
        neuf = self.niveau.flow_ids.filtered(
            lambda f: f.source_id.code == "e2" and f.target_id.code == "t1")
        self.assertEqual(neuf.routing, "auto")
        self.assertTrue(neuf.bpmn_id.startswith(f"{self.niveau.bpmn_id}_flow"))
        self.niveau.supprimer_flux("e2", "t1")
        self.assertEqual(len(self.niveau.flow_ids), avant)

    def test_lien_vers_une_annotation_devient_une_association(self):
        """Une annotation ne se relie pas par un flux de séquence."""
        self.niveau.creer_flux("e", "n1")
        neuf = self.niveau.flow_ids.filtered(
            lambda f: f.source_id.code == "e" and f.target_id.code == "n1")
        self.assertEqual(neuf.routing, "assoc")

    def test_lien_deja_present_refuse(self):
        with self.assertRaisesRegex(UserError, "déjà reliés"):
            self.niveau.creer_flux("s", "g")

    def test_lien_sur_soi_meme_refuse(self):
        with self.assertRaisesRegex(UserError, "boucler"):
            self.niveau.creer_flux("t1", "t1")

    # --- ce que le tracé annonce -------------------------------------------------
    def test_rendu_annonce_de_quoi_editer(self):
        rendu = self.niveau.rendu()
        self.assertTrue(rendu["modifiable"])
        self.assertFalse(rendu["gele"])
        self.assertEqual(rendu["pas"], self.niveau.pas_grille)
        self.assertEqual(rendu["col_w"], self.niveau.col_w)
        self.assertTrue(any(g["code"] == "task" for g in rendu["palette"]))
        # les couloirs portent leur code : c'est ce qui permet de savoir dans
        # quelle bande une forme a été lâchée
        self.assertEqual(sorted(c["code"] for c in rendu["couloirs"]), ["f", "t"])
        # les liens portent leurs extrémités : c'est ce qui permet de les couper
        self.assertTrue(all("src" in f and "tgt" in f for f in rendu["flux"]))
