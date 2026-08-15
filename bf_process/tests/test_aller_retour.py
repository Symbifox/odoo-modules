# -*- coding: utf-8 -*-
"""Ce qui entre doit ressortir, et les exports doivent tenir debout."""
from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged

CARTE = [
    {
        "title": "Traiter une demande",
        "level": "Niveau 1",
        "pool": "Blue Fox",
        "col_w": 180.0, "row_h": 120.0, "lane_pad": 56.0, "ext_header": 78.0,
        "lanes": [{"id": "f", "name": "Conseil"}, {"id": "t", "name": "Technique"}],
        "ext": [{"id": "cli", "name": "Client", "pos": "top"}],
        "nodes": [
            {"id": "s", "kind": "msgStart", "name": "Recevoir la demande",
             "col": 0, "row": 0, "lane": "f"},
            {"id": "g", "kind": "xor", "name": "Demande recevable ?",
             "col": 1, "row": 0, "lane": "f"},
            {"id": "t1", "kind": "task", "name": "Qualifier la demande",
             "col": 2, "row": 0, "lane": "t"},
            {"id": "e", "kind": "end", "name": "Demande qualifiée",
             "col": 3, "row": 0, "lane": "t"},
            {"id": "e2", "kind": "end", "name": "Demande écartée",
             "col": 1, "row": 1, "lane": "f"},
            {"id": "n1", "kind": "note", "tone": "risk", "w": 228.0, "h": 46.0,
             "name": "Aucun délai n'a été évoqué en séance.",
             "col": 2.2, "row": 1.1, "lane": "t"},
        ],
        "flows": [
            {"src": "s", "tgt": "g"},
            {"src": "g", "tgt": "t1", "label": "Oui", "lp": (0.0, -4.0), "lw": 96.0},
            {"src": "g", "tgt": "e2", "r": "v", "label": "Non"},
            {"src": "t1", "tgt": "e"},
            {"src": "t1", "tgt": "n1", "r": "assoc", "a": "B", "b": "T"},
        ],
        "msgs": [
            {"node": "s", "pool": "cli", "dir": "in", "label": "Demande", "lx": 8.0},
        ],
    },
]


@tagged("post_install", "-at_install")
class TestAllerRetour(TransactionCase):

    def setUp(self):
        super().setUp()
        self.processus = self.env["bf.process"].create({
            "name": "Essai", "code": "essai", "pool_name": "Blue Fox"})
        self.processus._charger_niveaux(CARTE)
        self.niveau = self.processus.diagram_ids

    def test_aller_retour_identique(self):
        """Le dict ressorti des enregistrements est celui qui est entré."""
        sorti = self.processus.to_dicts()
        self.assertEqual(len(sorti), 1)
        attendu, obtenu = dict(CARTE[0]), dict(sorti[0])
        # `dir: out` est la valeur par défaut du rendu : absente ou explicite,
        # le tracé est le même. On compare donc les deux formes normalisées.
        for d in (attendu, obtenu):
            for m in d["msgs"]:
                m.setdefault("dir", "out")
        self.assertEqual(attendu["nodes"], obtenu["nodes"])
        self.assertEqual(attendu["flows"], obtenu["flows"])
        self.assertEqual(attendu["msgs"], obtenu["msgs"])
        self.assertEqual(attendu["lanes"], obtenu["lanes"])
        self.assertEqual(attendu["ext"], obtenu["ext"])
        self.assertEqual(attendu["pool"], obtenu["pool"])

    def test_export_bpmn(self):
        xml = self.processus.exporter_bpmn()
        self.assertIn("<bpmn:definitions", xml)
        self.assertEqual(xml.count("<bpmn:sequenceFlow"), 4)
        self.assertEqual(xml.count("<bpmn:association"), 1)
        self.assertEqual(xml.count("<bpmn:messageFlow"), 1)
        self.assertEqual(xml.count("<bpmn:lane "), 2)
        self.assertEqual(xml.count("<bpmndi:BPMNShape"), 10)  # 6 nœuds + 2 pools + 2 couloirs
        self.assertIn("Recevoir la demande", xml)

    def test_export_mxgraph(self):
        xml = self.processus.exporter_mxgraph()
        self.assertIn("<mxfile", xml)
        self.assertIn("shape=mxgraph.bpmn.task2", xml)
        # le ton de l'annotation survit à l'export mxGraph, contrairement au BPMN
        self.assertIn("strokeColor=#D69921", xml)
        self.assertEqual(xml.count('edge="1"'), 6)

    def test_identifiants_bpmn_stables(self):
        """Un identifiant BPMN ne bouge pas : c'est lui qui évite les doublons."""
        noeud = self.niveau.node_ids.filtered(lambda n: n.code == "t1")
        self.assertEqual(noeud.bpmn_id, "d1_t1")
        avant = {n.code: n.bpmn_id for n in self.niveau.node_ids}
        noeud.write({"name": "Qualifier et documenter la demande"})
        self.assertEqual({n.code: n.bpmn_id for n in self.niveau.node_ids}, avant)

    def test_annotation_sans_hauteur_refusee(self):
        """Le serveur ne mesure pas le texte : il refuse plutôt que d'inventer."""
        carte = [dict(CARTE[0])]
        carte[0]["nodes"] = [dict(n) for n in CARTE[0]["nodes"]]
        carte[0]["nodes"][-1].pop("h")
        autre = self.env["bf.process"].create({
            "name": "Essai sans hauteur", "pool_name": "Blue Fox"})
        # viser le motif, pas « une exception » : ce test passait pour une tout
        # autre raison avant que l'unicité des identifiants soit corrigée.
        with self.assertRaisesRegex(UserError, "hauteur"):
            autre._charger_niveaux(carte)

    def test_annotation_reliee_par_association(self):
        noeuds = {n.code: n for n in self.niveau.node_ids}
        with self.assertRaises(ValidationError):
            self.env["bf.process.flow"].create({
                "diagram_id": self.niveau.id, "bpmn_id": "d1_flowX",
                "source_id": noeuds["e"].id, "target_id": noeuds["n1"].id,
                "routing": "auto"})

    def test_niveau_enfant_raccorde_au_sous_processus(self):
        """Une page enfant se raccroche au sous-processus qui porte son titre."""
        carte = [
            {"title": "Vue d'ensemble", "pool": "Blue Fox", "ext_header": 62.0,
             "nodes": [
                 {"id": "s", "kind": "start", "name": "Départ", "col": 0, "row": 0},
                 {"id": "a", "kind": "sub", "name": "Traiter le dossier",
                  "col": 1, "row": 0},
                 {"id": "e", "kind": "end", "name": "Dossier traité",
                  "col": 2, "row": 0}],
             "flows": [{"src": "s", "tgt": "a"}, {"src": "a", "tgt": "e"}]},
            {"title": "Traiter le dossier", "pool": "Blue Fox", "ext_header": 62.0,
             "nodes": [
                 {"id": "s", "kind": "start", "name": "Dossier ouvert",
                  "col": 0, "row": 0},
                 {"id": "e", "kind": "end", "name": "Dossier traité",
                  "col": 1, "row": 0}],
             "flows": [{"src": "s", "tgt": "e"}]},
        ]
        p = self.env["bf.process"].create({"name": "Deux niveaux",
                                           "pool_name": "Blue Fox"})
        p._charger_niveaux(carte)
        enfant = p.diagram_ids[1]
        appelants = enfant.parent_node_ids
        self.assertEqual(appelants.mapped("code"), ["a"])
        self.assertEqual(appelants.diagram_id, p.diagram_ids[0])

    def test_sous_processus_commun_a_plusieurs_appelants(self):
        """Une page peut être ouverte de deux endroits, et les deux comptent."""
        carte = [
            {"title": "Vue d'ensemble", "pool": "Blue Fox", "ext_header": 62.0,
             "nodes": [
                 {"id": "s", "kind": "start", "name": "Départ", "col": 0, "row": 0},
                 {"id": "a", "kind": "sub", "name": "Vérifier le dossier",
                  "col": 1, "row": 0},
                 {"id": "b", "kind": "sub", "name": "Vérifier le dossier",
                  "col": 2, "row": 0},
                 {"id": "e", "kind": "end", "name": "Dossier clos",
                  "col": 3, "row": 0}],
             "flows": [{"src": "s", "tgt": "a"}, {"src": "a", "tgt": "b"},
                       {"src": "b", "tgt": "e"}]},
            {"title": "Vérifier le dossier", "pool": "Blue Fox", "ext_header": 62.0,
             "nodes": [
                 {"id": "s", "kind": "start", "name": "Vérification demandée",
                  "col": 0, "row": 0},
                 {"id": "e", "kind": "end", "name": "Dossier vérifié",
                  "col": 1, "row": 0}],
             "flows": [{"src": "s", "tgt": "e"}]},
        ]
        p = self.env["bf.process"].create({"name": "Sous-processus commun",
                                           "pool_name": "Blue Fox"})
        p._charger_niveaux(carte)
        enfant = p.diagram_ids[1]
        self.assertEqual(sorted(enfant.parent_node_ids.mapped("code")), ["a", "b"])
        self.assertEqual(enfant.parent_count, 2)
        subs = p.diagram_ids.mapped("node_ids").filtered(lambda n: n.kind == "sub")
        self.assertFalse(subs.filtered(lambda n: not n.child_diagram_id))
