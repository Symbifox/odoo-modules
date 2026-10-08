# -*- coding: utf-8 -*-
"""Le réimport fusionnant : un fichier retouché revient dans SA carte.

Deux familles de contrôles vivent ici. D'abord la fidélité de la lecture — ce
qui sort en `.bpmn` doit se relire à l'identique, sinon toute fusion commence
par un tas de faux écarts. Ensuite la fusion elle-même : ce qu'elle propose, ce
qu'elle refuse de faire toute seule, et ce qu'elle conserve.

Le contrôle qui compte le plus est `test_lecture_rend_les_memes_coordonnees` :
il attache l'inversion des coordonnées au moteur de géométrie. `geometrie` cale
le bord gauche du pool sur un facteur qui lui appartient ; si ce facteur change,
c'est ce test qui doit tomber, et pas une fusion en production.
"""
import base64

from odoo.exceptions import UserError
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
            {"id": "n1", "kind": "note", "w": 228.0, "h": 46.0,
             "name": "Aucun délai n'a été évoqué en séance.",
             "col": 2.2, "row": 1.1, "lane": "t"},
        ],
        "flows": [
            {"src": "s", "tgt": "g"},
            {"src": "g", "tgt": "t1", "label": "Oui"},
            {"src": "g", "tgt": "e2", "label": "Non"},
            {"src": "t1", "tgt": "e"},
            {"src": "t1", "tgt": "n1", "r": "assoc", "a": "B", "b": "T"},
        ],
        "msgs": [
            {"node": "s", "pool": "cli", "dir": "in", "label": "Demande"},
        ],
    },
]

DEUX_NIVEAUX = [
    {"title": "Vue d'ensemble", "pool": "Blue Fox", "ext_header": 62.0,
     "nodes": [
         {"id": "s", "kind": "start", "name": "Départ", "col": 0, "row": 0},
         {"id": "a", "kind": "sub", "name": "Traiter le dossier",
          "col": 1, "row": 0},
         {"id": "e", "kind": "end", "name": "Dossier traité", "col": 2, "row": 0}],
     "flows": [{"src": "s", "tgt": "a"}, {"src": "a", "tgt": "e"}]},
    {"title": "Traiter le dossier", "pool": "Blue Fox", "ext_header": 62.0,
     "nodes": [
         {"id": "s", "kind": "start", "name": "Dossier ouvert", "col": 0, "row": 0},
         {"id": "e", "kind": "end", "name": "Dossier traité", "col": 1, "row": 0}],
     "flows": [{"src": "s", "tgt": "e"}]},
]


@tagged("post_install", "-at_install")
class TestFusion(TransactionCase):

    def setUp(self):
        super().setUp()
        self.processus = self.env["bf.process"].create({
            "name": "Essai fusion", "code": "fus", "pool_name": "Blue Fox"})
        self.processus._charger_niveaux(CARTE)
        self.niveau = self.processus.diagram_ids

    # ------------------------------------------------------------- outillage
    def _lire(self, xml=None, avec_grille=True):
        """Lit comme la fusion lit : avec le pas connu de chaque niveau."""
        grilles = (self.env["bf.process.merge.wizard"]._grilles(self.processus)
                   if avec_grille else None)
        return self.env["bf.process.lecture"]._lire_fichier(
            (xml or self.processus.exporter_bpmn()).encode("utf-8"),
            grilles=grilles)

    def _assistant(self, xml=None, processus=None):
        return self.env["bf.process.merge.wizard"].create({
            "process_id": (processus or self.processus).id,
            "fichier": base64.b64encode(
                (xml or self.processus.exporter_bpmn()).encode("utf-8")),
            "nom_fichier": "retouche.bpmn"})

    def _ecarts(self, xml=None, processus=None):
        wiz = self._assistant(xml, processus)
        wiz.action_analyser()
        return wiz

    # ===================================================== fidélité du retour
    def test_lecture_rend_les_memes_coordonnees(self):
        """Ce qui sort en `.bpmn` se relit sans bouger d'un pas de grille.

        Contrôle charnière : il lie l'inversion des coordonnées au facteur de
        marge que `geometrie` applique au bord gauche du pool. Si ce facteur
        change sans que la lecture suive, c'est ici que ça se voit.
        """
        lu = self._lire()[0]
        avant = {n["id"]: n for n in CARTE[0]["nodes"]}
        for n in lu["nodes"]:
            attendu = avant[n["id"]]
            self.assertAlmostEqual(n["col"], attendu["col"], places=3,
                                   msg=f"colonne de {n['id']}")
            self.assertAlmostEqual(n["row"], attendu["row"], places=3,
                                   msg=f"rangée de {n['id']}")
            self.assertEqual(n.get("lane"), attendu.get("lane"),
                             f"couloir de {n['id']}")
            self.assertEqual(n["kind"], attendu["kind"])

    def test_sans_grille_connue_le_pas_se_deduit_et_peut_glisser(self):
        """Pourquoi la fusion impose le pas qu'elle connaît déjà.

        Livré à lui-même, le lecteur déduit la largeur de colonne du plus
        petit écart horizontal qu'il observe. Sur cette carte, l'annotation
        est posée à 2,2 colonnes : l'écart le plus court n'est donc plus une
        colonne pleine, et tout se lit décalé. C'est sans conséquence pour une
        carte neuve — elle est cohérente avec elle-même — et ce serait fatal
        pour une fusion, qui compterait chaque nœud comme déplacé.
        """
        lu = self._lire(avec_grille=False)[0]
        self.assertNotEqual(lu["col_w"], 180.0)
        porte = [n for n in lu["nodes"] if n["id"] == "g"][0]
        self.assertNotAlmostEqual(porte["col"], 1.0, places=3)
        # et avec le pas connu, la même lecture retombe juste
        avec = [n for n in self._lire()[0]["nodes"] if n["id"] == "g"][0]
        self.assertAlmostEqual(avec["col"], 1.0, places=3)

    def test_lecture_conserve_les_codes_de_couloir_et_de_pool(self):
        """`d1_lane_f` redevient `f`, pas `lane_f`.

        L'export préfixe la famille en plus du niveau. Ne retirer que le
        niveau faisait revenir chaque couloir sous un code neuf, et toute
        comparaison le lisait comme un couloir retiré plus un ajouté.
        """
        lu = self._lire()[0]
        self.assertEqual([ln["id"] for ln in lu["lanes"]], ["f", "t"])
        self.assertEqual([p["id"] for p in lu["ext"]], ["cli"])
        self.assertEqual(lu["bpmn_id"], "d1")

    def test_lecture_conserve_la_surcharge_de_taille(self):
        """Une annotation élargie à la main revient élargie."""
        note = [n for n in self._lire()[0]["nodes"] if n["kind"] == "note"][0]
        self.assertEqual(note["w"], 228.0)
        self.assertEqual(note["h"], 46.0)

    def test_lecture_rend_l_annotation_a_son_couloir(self):
        """Une annotation n'est pas un `flowNodeRef`, et appartient pourtant
        à un couloir. La bande qui la contient tranche."""
        note = [n for n in self._lire()[0]["nodes"] if n["kind"] == "note"][0]
        self.assertEqual(note.get("lane"), "t")

    # ========================================================= fusion à blanc
    def test_fusion_de_son_propre_export_ne_dit_rien(self):
        """Le contrôle qui rend tous les autres lisibles : zéro écart."""
        wiz = self._ecarts()
        self.assertEqual(
            wiz.ecart_count, 0,
            "écarts inattendus : %s" % [
                (l.portee, l.operation, l.cle, l.avant, l.apres)
                for l in wiz.line_ids])

    # ============ ce que seule une vraie carte avait révélé ==============
    #
    # Les quatre contrôles qui suivent sont nés du passage de la fusion sur la
    # cartographie de référence du banc — quinze niveaux, cent soixante-treize
    # rendait deux cent cinquante-cinq écarts là où rien n'avait bougé. Aucun
    # ne se voyait sur la petite carte d'essai : ils tiennent à des cas que six
    # nœuds bien rangés ne produisent jamais.

    def _carte_veridique(self):
        """Une carte qui porte les cas que la carte d'essai n'a pas."""
        carte = [{
            # un titre qui contient lui-même un tiret cadratin
            "title": "Service à la clientèle — vue d'ensemble",
            "pool": "Blue Fox", "ext_header": 62.0,
            "lanes": [{"id": "a", "name": "Conseil"},
                      {"id": "b", "name": "Technique"}],
            "nodes": [
                {"id": "s", "kind": "start", "name": "Départ",
                 "col": 0, "row": 0, "lane": "a"},
                {"id": "t1", "kind": "task", "name": "Faire",
                 "col": 1, "row": 0, "lane": "b"},
                # surcharge de taille qui vaut EXACTEMENT la taille naturelle :
                # le fichier ne peut pas l'en distinguer
                {"id": "t2", "kind": "task", "name": "Vérifier",
                 "col": 1, "row": 1, "lane": "a", "w": 152.0, "h": 68.0},
                # aucun couloir : le tracé le range dans le premier
                {"id": "e", "kind": "end", "name": "Fini", "col": 2, "row": 0},
                # une annotation sans hauteur : la mesure fait foi
                {"id": "n1", "kind": "note", "tone": "risk",
                 "name": "Point à confirmer en séance.",
                 "col": 1.2, "row": 1, "lane": "b"},
            ],
            "flows": [{"src": "s", "tgt": "t1"}, {"src": "t1", "tgt": "e"},
                      {"src": "t1", "tgt": "n1", "r": "assoc",
                       "a": "B", "b": "T"}],
        }]
        p = self.env["bf.process"].create({
            "name": "Carte véridique", "pool_name": "Blue Fox"})
        p._charger_niveaux(carte)
        return p

    def test_titre_avec_tiret_cadratin_ne_se_fait_pas_decapiter(self):
        """« Service à la clientèle — vue d'ensemble » n'est pas « vue d'ensemble ».

        L'export accole le libellé de niveau au titre par un tiret cadratin.
        Découper sur le dernier séparateur pour les séparer décapite tout titre
        qui en contient déjà un.
        """
        p = self._carte_veridique()
        xml = p.exporter_bpmn()
        # ce que le lecteur en retire — c'est ce titre-là qui sert à CRÉER une
        # page, et qui serait donc écrit tronqué
        lu = self.env["bf.process.lecture"]._lire_fichier(xml.encode("utf-8"))[0]
        self.assertEqual(lu["title"], "Service à la clientèle — vue d'ensemble")
        # et la comparaison, qui porte sur le nom du diagramme, reste muette
        titres = self._ecarts(xml, processus=p).line_ids.filtered(
            lambda l: l.portee == "niveau")
        self.assertFalse(
            titres, "titre altéré : %s" % [(l.avant, l.apres) for l in titres])

    def test_libelle_de_niveau_reste_separe_du_titre(self):
        """« Niveau 2 — Traiter » se rescinde ; un titre à tiret, non.

        Les deux cas passent par la même chaîne exportée, et seul le motif du
        premier segment permet de les départager.
        """
        Lecture = self.env["bf.process.lecture"]
        self.assertEqual(Lecture._scinder("Niveau 2 — Traiter le dossier"),
                         ("Niveau 2", "Traiter le dossier"))
        self.assertEqual(Lecture._scinder("Service à la clientèle — vue d'ensemble"),
                         (None, "Service à la clientèle — vue d'ensemble"))

    def test_noeud_sans_couloir_reste_sans_ecart(self):
        """Un `lane_id` vide et « le premier couloir » dessinent la même chose.

        L'export range dans le premier couloir tout nœud qui n'en déclare
        aucun — c'est déjà ce que fait le moteur de géométrie. Les traiter
        comme deux états différents produisait un changement de couloir ET un
        déplacement pour chacun de ces nœuds.
        """
        p = self._carte_veridique()
        wiz = self._ecarts(p.exporter_bpmn(), processus=p)
        bruit = wiz.line_ids.filtered(
            lambda l: l.operation in ("couloir", "position"))
        self.assertFalse(
            bruit, "écarts fantômes : %s" % [(l.cle, l.operation, l.avant,
                                              l.apres) for l in bruit])

    def test_taille_naturelle_ne_fait_pas_ecart(self):
        """Une surcharge qui vaut la taille naturelle n'existe pas au fichier.

        Le `.bpmn` porte des bornes, pas la distinction entre « par défaut » et
        « surchargé à la valeur par défaut ». C'est donc la boîte dessinée
        qu'il faut comparer, jamais le champ de surcharge.
        """
        p = self._carte_veridique()
        wiz = self._ecarts(p.exporter_bpmn(), processus=p)
        self.assertFalse(wiz.line_ids.filtered(lambda l: l.operation == "taille"))

    def test_ton_d_annotation_ni_compare_ni_perdu(self):
        """Le BPMN ne transporte pas le ton : son absence n'est pas un retrait."""
        p = self._carte_veridique()
        note = p.mapped("diagram_ids.node_ids").filtered(
            lambda n: n.kind == "note")
        self.assertEqual(note.tone, "risk")
        wiz = self._ecarts(p.exporter_bpmn(), processus=p)
        self.assertEqual(
            wiz.ecart_count, 0,
            "écarts inattendus : %s" % [(l.portee, l.operation, l.cle)
                                        for l in wiz.line_ids])
        self.assertEqual(note.tone, "risk")

    # ============================================================ renommages
    def test_renommage_detecte_et_applique_sans_perdre_le_noeud(self):
        """Un nœud renommé reste LE MÊME enregistrement.

        C'est toute la différence avec un réimport : le fil de discussion, les
        pièces jointes et le registre de validation tiennent à l'identité de
        l'enregistrement, pas à son libellé.
        """
        noeud = self.niveau.node_ids.filtered(lambda n: n.code == "t1")
        noeud.message_post(body="Photo de l'atelier à joindre.")
        noeud.valide_proprietaire = True
        avant_id = noeud.id

        xml = self.processus.exporter_bpmn().replace(
            "Qualifier la demande", "Qualifier et documenter la demande")
        wiz = self._ecarts(xml)
        lignes = wiz.line_ids.filtered(
            lambda l: l.portee == "noeud" and l.operation == "renommage")
        self.assertEqual(len(lignes), 1)
        self.assertEqual(lignes.cle, "t1")
        self.assertEqual(lignes.decision, "appliquer")

        wiz.action_appliquer()
        self.assertEqual(noeud.name, "Qualifier et documenter la demande")
        self.assertEqual(noeud.id, avant_id)
        self.assertTrue(noeud.valide_proprietaire)
        self.assertTrue(noeud.message_ids.filtered(
            lambda m: "atelier" in (m.body or "")))

    def test_ecart_ignore_ne_s_applique_pas(self):
        xml = self.processus.exporter_bpmn().replace(
            "Qualifier la demande", "Autre chose")
        wiz = self._ecarts(xml)
        wiz.action_tout_ignorer()
        with self.assertRaises(UserError):
            wiz.action_appliquer()
        self.assertEqual(
            self.niveau.node_ids.filtered(lambda n: n.code == "t1").name,
            "Qualifier la demande")

    # ============================================================== retraits
    def test_retrait_propose_mais_ignore_par_defaut(self):
        """Un nœud absent du fichier ne disparaît pas tout seul."""
        xml = self.processus.exporter_bpmn()
        # on retire l'annotation : son élément, sa forme et son association
        for morceau in ('<bpmn:textAnnotation id="d1_n1">',):
            self.assertIn(morceau, xml)
        xml = self._sans_annotation(xml)

        wiz = self._ecarts(xml)
        retraits = wiz.line_ids.filtered(lambda l: l.operation == "retrait")
        self.assertTrue(retraits)
        self.assertTrue(all(l.decision == "ignorer" for l in retraits))
        wiz.action_appliquer() if wiz.retenu_count else None
        self.assertTrue(self.niveau.node_ids.filtered(lambda n: n.code == "n1"))

    def test_retrait_applique_quand_on_le_decide(self):
        xml = self._sans_annotation(self.processus.exporter_bpmn())
        wiz = self._ecarts(xml)
        wiz.line_ids.filtered(
            lambda l: l.operation == "retrait"
            and l.portee == "noeud").decision = "appliquer"
        wiz.line_ids.filtered(
            lambda l: l.operation == "retrait"
            and l.portee == "flux").decision = "appliquer"
        wiz.action_appliquer()
        self.assertFalse(self.niveau.node_ids.filtered(lambda n: n.code == "n1"))

    def _sans_annotation(self, xml):
        """Retire l'annotation `n1`, sa forme et son association du fichier."""
        import re
        xml = re.sub(r"    <bpmn:textAnnotation id=\"d1_n1\">.*?</bpmn:textAnnotation>\n",
                     "", xml, flags=re.S)
        xml = re.sub(r"    <bpmn:association [^>]*targetRef=\"d1_n1\"/>\n", "", xml)
        xml = re.sub(r"    <bpmndi:BPMNShape id=\"d1_n1_di\".*?</bpmndi:BPMNShape>\n",
                     "", xml, flags=re.S)
        xml = re.sub(r"    <bpmndi:BPMNEdge id=\"d1_flow5_di\".*?</bpmndi:BPMNEdge>\n",
                     "", xml, flags=re.S)
        return xml

    # ============================================================== ajouts
    def test_noeud_ajoute_dans_le_fichier_est_cree_dans_son_couloir(self):
        xml = self.processus.exporter_bpmn().replace(
            '<bpmn:task id="d1_t1" name="Qualifier la demande">',
            '<bpmn:task id="d1_t9" name="Consigner la demande"/>\n'
            '    <bpmn:task id="d1_t1" name="Qualifier la demande">')
        xml = xml.replace(
            '<bpmndi:BPMNShape id="d1_t1_di" bpmnElement="d1_t1">',
            '<bpmndi:BPMNShape id="d1_t9_di" bpmnElement="d1_t9">\n'
            '      <dc:Bounds x="700.0" y="400.0" width="152.0" height="68.0"/>\n'
            '    </bpmndi:BPMNShape>\n'
            '    <bpmndi:BPMNShape id="d1_t1_di" bpmnElement="d1_t1">')
        wiz = self._ecarts(xml)
        ajouts = wiz.line_ids.filtered(
            lambda l: l.portee == "noeud" and l.operation == "ajout")
        self.assertEqual(ajouts.cle, "t9")
        wiz.action_appliquer()
        cree = self.niveau.node_ids.filtered(lambda n: n.code == "t9")
        self.assertEqual(cree.name, "Consigner la demande")
        self.assertEqual(cree.bpmn_id, "d1_t9")

    # ============================================================== ancrage
    def _fichier_avec_deplacement(self, code, col):
        """Le `.bpmn` de la carte, un seul nœud déplacé — la carte, elle, ne
        bouge pas."""
        noeud = self.niveau.node_ids.filtered(lambda n: n.code == code)
        avant = noeud.col
        noeud.col = col
        xml = self.processus.exporter_bpmn()
        noeud.col = avant
        return xml

    def test_ancrage_resiste_au_deplacement_du_noeud_le_plus_a_gauche(self):
        """Déplacer le nœud le plus à gauche ne déplace pas toute la page.

        Les coordonnées lues sont relatives au nœud le plus à gauche : dès que
        celui-ci bouge, toute la page se relit décalée. Le calage doit annuler
        ce décalage, sans quoi les cinq nœuds immobiles passeraient pour
        déplacés.
        """
        wiz = self._ecarts(self._fichier_avec_deplacement("s", -1.0))
        positions = wiz.line_ids.filtered(lambda l: l.operation == "position")
        self.assertEqual(
            len(positions), 1,
            "un seul nœud a bougé : %s" % [(l.cle, l.avant, l.apres)
                                           for l in positions])
        self.assertEqual(positions.cle, "s")
        wiz.action_appliquer()
        self.assertAlmostEqual(
            self.niveau.node_ids.filtered(lambda n: n.code == "s").col, -1.0,
            places=3)
        self.assertAlmostEqual(
            self.niveau.node_ids.filtered(lambda n: n.code == "e").col, 3.0,
            places=3)

    def test_ancrage_suit_la_majorite_et_non_l_extreme(self):
        """Le calage retenu est celui qui laisse le PLUS de nœuds en place.

        Un nœud isolé qui part loin à droite ne doit pas entraîner le repère
        avec lui. Prendre l'écart extrême plutôt que le plus fréquent aurait
        déplacé les cinq autres pour suivre le seul qui a bougé — et le
        déplacement d'un nœud se serait lu comme un remaniement complet.
        """
        wiz = self._ecarts(self._fichier_avec_deplacement("e", 5.0))
        positions = wiz.line_ids.filtered(lambda l: l.operation == "position")
        self.assertEqual(
            len(positions), 1,
            "seul « e » a bougé : %s" % [(l.cle, l.avant, l.apres)
                                         for l in positions])
        self.assertEqual(positions.cle, "e")
        wiz.action_appliquer()
        self.assertAlmostEqual(
            self.niveau.node_ids.filtered(lambda n: n.code == "e").col, 5.0,
            places=3)
        for code, attendu in (("s", 0.0), ("g", 1.0), ("t1", 2.0)):
            self.assertAlmostEqual(
                self.niveau.node_ids.filtered(lambda n: n.code == code).col,
                attendu, places=3, msg=f"« {code} » n'aurait pas dû bouger")

    # ====================================================== sous-processus
    def test_sous_processus_renomme_garde_sa_page(self):
        """Le lien vers la page enfant se fait par titre, et survit au renommage.

        C'est le piège que le fichier ne peut pas éviter : le BPMN ne
        transporte pas le chaînage. Refaire le raccord par titre après un
        renommage dans un éditeur tiers casserait le lien en silence — donc on
        ne refait pas le raccord des liens qui tiennent déjà.
        """
        p = self.env["bf.process"].create({
            "name": "Deux niveaux", "pool_name": "Blue Fox"})
        p._charger_niveaux(DEUX_NIVEAUX)
        appelant = p.mapped("diagram_ids.node_ids").filtered(
            lambda n: n.kind == "sub")
        enfant = appelant.child_diagram_id
        self.assertTrue(enfant)

        # le sous-processus SEUL est renommé ; la page garde son titre, si
        # bien qu'aucun appariement par titre ne peut plus les rapprocher
        xml = p.exporter_bpmn().replace(
            '<bpmn:subProcess id="d1_a" name="Traiter le dossier">',
            '<bpmn:subProcess id="d1_a" name="Instruire le dossier">')
        self.assertIn("Instruire le dossier", xml)
        self.assertIn('name="Traiter le dossier"', xml, "la page garde son titre")

        wiz = self._ecarts(xml, processus=p)
        wiz.action_appliquer()
        self.assertEqual(appelant.name, "Instruire le dossier")
        self.assertEqual(appelant.child_diagram_id, enfant,
                         "le lien vers la page enfant a été perdu")
        # et il n'est pas non plus SIGNALÉ comme sans page : refaire le
        # raccord sur un lien qui tient produirait un faux avertissement
        self.assertNotIn("n'ouvrent aucune page", p.message_ids[0].body)

    # ================================================== fichier partiel
    def test_fichier_partiel_avertit_et_ne_retire_rien(self):
        """Un éditeur qui ne réexporte qu'une page ne doit pas vider la carte."""
        p = self.env["bf.process"].create({
            "name": "Partiel", "pool_name": "Blue Fox"})
        p._charger_niveaux(DEUX_NIVEAUX)
        # seul le premier niveau part dans le fichier
        xml = self.env["bf.process"].browse(p.id).exporter_bpmn()
        from ..generateur import bpmn as gen
        xml = gen.to_bpmn([p.diagram_ids[0].to_dict()],
                          prefixes=[p.diagram_ids[0].bpmn_id])

        wiz = self._ecarts(xml, processus=p)
        self.assertIn("ne sont pas dans le fichier", wiz.resume_html)
        retraits = wiz.line_ids.filtered(
            lambda l: l.portee == "niveau" and l.operation == "retrait")
        self.assertEqual(len(retraits), 1)
        self.assertEqual(retraits.decision, "ignorer")
        self.assertEqual(len(p.diagram_ids), 2)

    # ================================================== refus et garde-fous
    def test_fichier_etranger_refuse(self):
        """Aucun niveau en commun : ce n'est pas une fusion, c'est un import."""
        autre = self.env["bf.process"].create({
            "name": "Ailleurs", "pool_name": "Autre"})
        autre._charger_niveaux(DEUX_NIVEAUX)
        # des identifiants qui ne peuvent croiser aucun de la carte visée
        xml = autre.exporter_bpmn().replace("d1_", "zz1_").replace("d2_", "zz2_")
        wiz = self._assistant(xml)
        with self.assertRaisesRegex(UserError, "identifiants"):
            wiz.action_analyser()

    def test_version_validee_refuse_la_fusion(self):
        self.processus.action_valider()
        wiz = self._assistant()
        with self.assertRaisesRegex(UserError, "validée"):
            wiz.action_analyser()

    def test_carte_modifiee_entre_analyse_et_application(self):
        """Une analyse calculée contre un état disparu ne s'applique pas."""
        xml = self.processus.exporter_bpmn().replace(
            "Qualifier la demande", "Qualifier et documenter")
        wiz = self._ecarts(xml)
        self.niveau.node_ids.filtered(lambda n: n.code == "e").name = "Autre fin"
        with self.assertRaisesRegex(UserError, "a changé depuis l'analyse"):
            wiz.action_appliquer()

    # ============ ce qu'une relecture adverse a trouvé =====================
    #
    # Un `.bpmn` n'arrive pas forcément de chez nous, et un éditeur tiers n'a
    # pas besoin d'être malveillant pour écrire salement. Ces contrôles
    # existent parce qu'un audit indépendant a reproduit six fichiers qui
    # sortaient en trace de 500 — ou, pire, qui passaient.

    MODELE = '''<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
 xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI"
 xmlns:dc="http://www.omg.org/spec/DD/20100524/DC"
 xmlns:di="http://www.omg.org/spec/DD/20100524/DI" id="D1">
  <bpmn:collaboration id="%(colab)s">
    <bpmn:participant id="d1_pool" name="P" processRef="d1_process"/>
  </bpmn:collaboration>
  <bpmn:process id="d1_process" isExecutable="false">
    <bpmn:task id="d1_t1" name="T"/>
  </bpmn:process>
  <bpmndi:BPMNDiagram id="d1_diagram" name="N">
    <bpmndi:BPMNPlane id="d1_plane" bpmnElement="%(colab)s">
      <bpmndi:BPMNShape id="d1_pool_di" bpmnElement="d1_pool">
        <dc:Bounds x="0" y="0" width="500" height="200"/>
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="d1_t1_di" bpmnElement="d1_t1">
        %(bornes)s
      </bpmndi:BPMNShape>
    </bpmndi:BPMNPlane>
  </bpmndi:BPMNDiagram>
</bpmn:definitions>'''
    BORNES_OK = '<dc:Bounds x="100" y="50" width="152" height="68"/>'

    def _bancal(self, colab="c1", bornes=None):
        return (self.MODELE % {"colab": colab,
                               "bornes": self.BORNES_OK if bornes is None
                               else bornes}).encode("utf-8")

    def test_fichier_bancal_refuse_lisiblement(self):
        """Un fichier mal formé se refuse, il ne sort pas en trace de 500.

        C'est la raison d'être du décorateur de refus lisible ; il ne couvrait
        que les refus de mesure, et laissait passer tout ce que le XML lui-même
        pouvait avoir de tordu.
        """
        Lecture = self.env["bf.process.lecture"]
        cas = [
            ("forme sans dc:Bounds", self._bancal(bornes="")),
            ("bornes sans attribut", self._bancal(bornes="<dc:Bounds/>")),
            ("borne non numérique",
             self._bancal(bornes='<dc:Bounds x="abc" y="5" width="1" height="1"/>')),
        ]
        for nom, xml in cas:
            with self.assertRaises(UserError, msg=nom):
                Lecture._lire_fichier(xml)

    def test_apostrophe_dans_un_identifiant_se_lit(self):
        """Un identifiant qui contient une apostrophe n'est pas une anomalie.

        La collaboration était retrouvée par un prédicat ElementPath composé
        en f-string : l'identifiant du fichier entrait donc dans la SYNTAXE de
        la requête, et une apostrophe — qu'un éditeur tiers produit sans
        malice — cassait le prédicat. Indexer les collaborations une fois
        supprime la question au lieu de l'échapper.
        """
        lu = self.env["bf.process.lecture"]._lire_fichier(self._bancal(colab="a'b"))
        self.assertEqual(len(lu), 1)
        self.assertEqual([n["id"] for n in lu[0]["nodes"]], ["t1"])

    def test_coordonnee_non_finie_refusee(self):
        """`nan` et `inf` se refusent, et c'est le contrôle qui compte le plus.

        Les deux se lisent sans lever quoi que ce soit et traversent toute
        l'arithmétique : ils s'écrivaient tels quels dans la colonne d'un
        nœud, et chaque tracé, export ou PDF ultérieur rejouait l'opération.
        Un plantage se voit ; celui-là ne se voyait pas.
        """
        Lecture = self.env["bf.process.lecture"]
        for valeur in ("nan", "inf", "-inf"):
            xml = self._bancal(
                bornes=f'<dc:Bounds x="{valeur}" y="5" width="152" height="68"/>')
            with self.assertRaisesRegex(UserError, valeur, msg=valeur):
                Lecture._lire_fichier(xml)

    def test_application_sans_analyse_refusee(self):
        """Les lignes d'arbitrage sont des enregistrements que le client écrit.

        Écrit « si une empreinte existe et diffère », le contrôle sautait en
        entier pour un assistant dont l'analyse n'avait jamais tourné.
        """
        wiz = self._assistant()
        wiz.write({"line_ids": [(0, 0, {
            "portee": "noeud", "operation": "renommage", "niveau": "d1",
            "cle": "t1", "libelle": "forgé", "decision": "appliquer"})]})
        self.assertFalse(wiz.empreinte)
        with self.assertRaisesRegex(UserError, "n'a pas été analysée"):
            wiz.action_appliquer()

    def test_application_d_un_fichier_etranger_refusee(self):
        """Le refus du fichier étranger vit aussi à l'application.

        Il n'existait qu'à l'analyse : par appel direct, un fichier sans aucun
        niveau commun se fusionnait quand même — en retirant tout pour tout
        remettre, ce que ce refus existe précisément pour empêcher.
        """
        autre = self.env["bf.process"].create({
            "name": "Ailleurs bis", "pool_name": "Autre"})
        autre._charger_niveaux(DEUX_NIVEAUX)
        etranger = autre.exporter_bpmn().replace("d1_", "zz1_").replace("d2_", "zz2_")

        # une analyse valide d'abord, pour obtenir une empreinte légitime
        wiz = self._ecarts(self.processus.exporter_bpmn().replace(
            "Qualifier la demande", "Autre libellé"))
        self.assertTrue(wiz.empreinte)
        # puis on lui substitue un fichier qui n'a rien à voir
        wiz.fichier = base64.b64encode(etranger.encode("utf-8"))
        with self.assertRaisesRegex(UserError, "Aucun niveau de ce fichier"):
            wiz.action_appliquer()
        self.assertEqual(len(self.processus.diagram_ids), 1,
                         "la carte n'a pas été touchée")

    def test_compte_rendu_n_execute_pas_un_nom_de_noeud(self):
        """Un libellé venu d'un fichier tiers ne devient jamais une balise.

        La leçon est déjà payée sur la comparaison de versions : un champ HTML
        composé par concaténation exécute ce qu'on lui donne. Ce qui doit être
        vérifié, c'est l'absence de la BALISE — le mot, lui, survit très bien
        en texte inerte.
        """
        piege = '<img src=x onerror=alert(1)>'
        xml = self.processus.exporter_bpmn().replace(
            "Qualifier la demande",
            piege.replace("<", "&lt;").replace(">", "&gt;"))
        wiz = self._ecarts(xml)
        wiz.action_appliquer()
        corps = self.processus.message_ids[0].body
        self.assertNotIn("<img", corps)
        self.assertIn("onerror", corps, "le texte reste lisible, inerte")

    # ================================== ce que la lecture laisse de côté
    #
    # Trouvé en éprouvant la lecture par mutation : sept classes de retouche
    # attrapées, une seule passait au travers — un nœud ajouté sans sa forme
    # DI, ignoré sans un mot, et l'analyse concluait « aucun écart ». La partie
    # graphique est pourtant facultative en BPMN 2.0 : un fichier valide peut
    # porter un élément qu'on ne sait pas placer. Il se signale, il ne se tait
    # pas.

    TACHE_T1 = '<bpmn:task id="d1_t1" name="Qualifier la demande">'

    def _sans_forme(self, xml, ident):
        """Retire la forme DI d'un élément, et elle seule : sa sémantique reste."""
        import re
        xml, n = re.subn(
            r'    <bpmndi:BPMNShape id="[^"]*" bpmnElement="%s"[^>]*>.*?'
            r'</bpmndi:BPMNShape>\n' % re.escape(ident), "", xml, flags=re.S)
        self.assertEqual(n, 1, f"forme de {ident} introuvable : rien n'a été retouché")
        return xml

    def _avec_tache(self, xml, balise="task", nom="Archiver la demande",
                    lien=False):
        """Ajoute `d1_t9` au processus, SANS forme DI ; `lien` le relie à t1."""
        self.assertIn(self.TACHE_T1, xml)
        xml = xml.replace(self.TACHE_T1, f'<bpmn:{balise} id="d1_t9" name="{nom}"/>'
                          f'\n    {self.TACHE_T1}')
        if lien:
            xml = xml.replace(
                "  </bpmn:process>",
                '    <bpmn:sequenceFlow id="d1_flow9" sourceRef="d1_t1" '
                'targetRef="d1_t9"/>\n  </bpmn:process>')
            self.assertIn('id="d1_flow9"', xml)
        return xml

    def test_noeud_neuf_sans_forme_signale_et_non_tu(self):
        """La retouche que la mutation avait laissée passer : elle se dit."""
        wiz = self._ecarts(self._avec_tache(self.processus.exporter_bpmn()))
        self.assertEqual(wiz.ecart_count, 0, "rien à arbitrer : rien à placer")
        resume = str(wiz.resume_html)
        self.assertIn("1 élément(s) du fichier n'ont pas été lus", resume)
        self.assertIn("tâche « Archiver la demande »", resume)
        self.assertIn("partie graphique (DI)", resume)
        self.assertNotIn("le fichier dit exactement ce que la carte dit", resume)

    def test_lien_vers_un_noeud_sans_forme_ecarte_avec_lui(self):
        """Un lien vers un nœud non lu n'est pas un ajout à proposer.

        Proposé, il partait « appliquer » par défaut, puis se faisait refuser
        à l'application faute de nœud à relier.
        """
        wiz = self._ecarts(self._avec_tache(
            self.processus.exporter_bpmn(), lien=True))
        self.assertFalse(wiz.line_ids.filtered(lambda l: l.portee == "flux"))
        resume = str(wiz.resume_html)
        self.assertIn("2 élément(s) du fichier n'ont pas été lus", resume)
        self.assertIn("lien de « Qualifier la demande » vers « Archiver la "
                      "demande »", resume)

    def test_import_d_un_lien_vers_un_noeud_sans_forme(self):
        """L'import ne plante plus, et dit ce qu'il n'a pas repris.

        Le lien gardé désignait un nœud que personne n'avait créé : un
        fichier BPMN valide sortait en trace de 500 (`KeyError`).
        """
        xml = self._avec_tache(self.processus.exporter_bpmn(), lien=True)
        wiz = self.env["bf.process.import.wizard"].create({
            "name": "Import partiel", "nom_fichier": "partiel.bpmn",
            "fichier": base64.b64encode(xml.encode("utf-8"))})
        p = self.env["bf.process"].browse(wiz.action_importer()["res_id"])
        self.assertEqual(sorted(p.mapped("diagram_ids.node_ids.code")),
                         sorted(n["id"] for n in CARTE[0]["nodes"]))
        self.assertEqual(len(p.mapped("diagram_ids.flow_ids")),
                         len(CARTE[0]["flows"]))
        corps = p.message_ids[0].body
        self.assertIn("2 élément(s) du fichier n'ont pas été lus", corps)
        self.assertIn("Archiver la demande", corps)

    def test_type_non_pris_en_charge_signale(self):
        """Dessiné dans le fichier, d'un type que le module ne trace pas.

        L'usager le voit dans son éditeur : le taire lui ferait croire que
        l'étape est revenue dans la carte.
        """
        xml = self._avec_tache(self.processus.exporter_bpmn(),
                               balise="serviceTask", nom="Appeler le service")
        xml = xml.replace(
            '    <bpmndi:BPMNShape id="d1_t1_di"',
            '    <bpmndi:BPMNShape id="d1_t9_di" bpmnElement="d1_t9">\n'
            '      <dc:Bounds x="700.0" y="416.0" width="152.0" height="68.0"/>\n'
            '    </bpmndi:BPMNShape>\n'
            '    <bpmndi:BPMNShape id="d1_t1_di"')
        self.assertIn('id="d1_t9_di"', xml)
        resume = str(self._ecarts(xml).resume_html)
        self.assertIn("élément « Appeler le service »", resume)
        self.assertIn("type BPMN « serviceTask »", resume)

    def test_element_connu_sans_forme_pas_propose_au_retrait(self):
        """Le fichier porte encore l'étape ; il n'a perdu que sa géométrie.

        Proposer son retrait — et celui de ses liens — dirait le contraire de
        ce que le fichier dit.
        """
        wiz = self._ecarts(self._sans_forme(
            self.processus.exporter_bpmn(), "d1_t1"))
        self.assertEqual(
            wiz.ecart_count, 0,
            "écarts inattendus : %s" % [(l.portee, l.operation, l.cle)
                                        for l in wiz.line_ids])
        resume = str(wiz.resume_html)
        # la tâche, et ses trois liens : entrant, sortant, annotation
        self.assertIn("4 élément(s) du fichier n'ont pas été lus", resume)
        self.assertIn("tâche « Qualifier la demande »", resume)
        self.assertIn("Aucun écart dans ce qui a pu être lu", resume)

    def test_participant_sans_forme_signale_avec_son_message(self):
        wiz = self._ecarts(self._sans_forme(
            self.processus.exporter_bpmn(), "d1_pool_cli"))
        self.assertEqual(
            wiz.ecart_count, 0,
            "écarts inattendus : %s" % [(l.portee, l.operation, l.cle)
                                        for l in wiz.line_ids])
        resume = str(wiz.resume_html)
        self.assertIn("participant « Client »", resume)
        self.assertIn("message « Demande »", resume)

    def test_couloir_sans_forme_garde_ses_noeuds(self):
        """Le couloir est nommé, et ses nœuds n'en sortent pas.

        Le fichier les y déclare encore par `flowNodeRef`. Les ranger selon
        leur seul centre les faisait tomber dans un autre couloir, et la
        fusion proposait de les y déplacer, à « Appliquer » par défaut.
        """
        for couloir, nom in (("d1_lane_f", "Conseil"), ("d1_lane_t", "Technique")):
            wiz = self._ecarts(self._sans_forme(
                self.processus.exporter_bpmn(), couloir))
            self.assertIn("couloir « %s »" % nom, str(wiz.resume_html))
            self.assertEqual(
                wiz.ecart_count, 0,
                "%s : écarts inattendus %s" % (couloir, [
                    (l.portee, l.operation, l.cle) for l in wiz.line_ids]))
        # Et ce qui a VRAIMENT bougé dans ce couloir se voit encore : le
        # nœud y est lu, pas seulement tenu à l'écart des comparaisons.
        wiz = self._ecarts(self._sans_forme(
            self._fichier_avec_deplacement("g", 2.0), "d1_lane_f"))
        self.assertEqual(
            [(l.operation, l.cle) for l in wiz.line_ids], [("position", "g")])

    def test_identifiants_absents_ne_plantent_pas(self):
        """`id` est facultatif dans le schéma BPMN 2.0.

        Un participant ou un couloir sans identifiant, un message sans
        cible : chacun se nomme, aucun ne fait tomber la lecture.
        """
        xml = self.processus.exporter_bpmn()
        for ancre, ajout in (
                ('<bpmn:participant id="d1_pool_cli" name="Client"/>',
                 '<bpmn:participant name="Fournisseur"/>'),
                ('<bpmn:lane id="d1_lane_t" name="Technique">',
                 '<bpmn:lane name="Vide"/>'),
                ('<bpmn:messageFlow id="d1_msg1"',
                 '<bpmn:messageFlow id="d1_msg9" name="Relance" sourceRef="d1_t1"/>')):
            self.assertIn(ancre, xml)
            xml = xml.replace(ancre, ajout + "\n    " + ancre)
        wiz = self._ecarts(xml)
        resume = str(wiz.resume_html)
        for attendu in ("participant « Fournisseur »", "couloir « Vide »",
                        "message « Relance »"):
            self.assertIn(attendu, resume)

    def test_import_d_un_participant_sans_forme_et_de_son_message(self):
        """Le message vers un participant écarté s'écarte avec lui.

        Gardé, il désignait un participant que personne n'avait créé : l'import
        sortait en trace de 500 (`KeyError`).
        """
        xml = self._sans_forme(self.processus.exporter_bpmn(), "d1_pool_cli")
        wiz = self.env["bf.process.import.wizard"].create({
            "name": "Import sans client", "nom_fichier": "sans-client.bpmn",
            "fichier": base64.b64encode(xml.encode("utf-8"))})
        p = self.env["bf.process"].browse(wiz.action_importer()["res_id"])
        self.assertFalse(p.mapped("diagram_ids.message_ids"))
        corps = p.message_ids[0].body
        self.assertIn("participant « Client »", corps)
        self.assertIn("message « Demande »", corps)

    def test_dessine_hors_du_processus_lu_signale(self):
        """L'intérieur d'un sous-processus déplié, une annotation posée sur la
        collaboration : dessinés, donc vus dans l'éditeur, donc nommés."""
        xml = self.processus.exporter_bpmn()
        self.assertIn(self.TACHE_T1, xml)
        xml = xml.replace(self.TACHE_T1, (
            '<bpmn:subProcess id="d1_sp" name="Bloc déplié">\n'
            '      <bpmn:task id="d1_sp_t" name="Étape intérieure"/>\n'
            '    </bpmn:subProcess>\n    ') + self.TACHE_T1)
        xml = xml.replace(
            "  </bpmn:collaboration>",
            '    <bpmn:textAnnotation id="d1_hors"><bpmn:text>Note de '
            'collaboration</bpmn:text></bpmn:textAnnotation>\n'
            "  </bpmn:collaboration>")
        formes = ""
        for ident, x in (("d1_sp", 860), ("d1_sp_t", 880), ("d1_hors", 900)):
            formes += ('    <bpmndi:BPMNShape id="%s_di" bpmnElement="%s">\n'
                       '      <dc:Bounds x="%d.0" y="416.0" width="152.0" '
                       'height="68.0"/>\n    </bpmndi:BPMNShape>\n' % (ident, ident, x))
        xml = xml.replace('    <bpmndi:BPMNShape id="d1_t1_di"',
                          formes + '    <bpmndi:BPMNShape id="d1_t1_di"')
        self.assertIn('id="d1_hors_di"', xml)
        resume = str(self._ecarts(xml).resume_html)
        self.assertIn("tâche « Étape intérieure »", resume)
        self.assertIn("annotation « Note de collaboration »", resume)
        self.assertIn("hors du processus que le module lit", resume)

    def test_type_non_trace_nomme_sa_definition_d_evenement(self):
        """Un `endEvent` simple se trace ; celui de terminaison, non. Le dire
        sans sa définition faisait lire « endEvent non pris en charge »."""
        xml = self.processus.exporter_bpmn().replace(
            self.TACHE_T1,
            '<bpmn:endEvent id="d1_stop" name="Tout arrêter">'
            '<bpmn:terminateEventDefinition/></bpmn:endEvent>\n    '
            + self.TACHE_T1)
        xml = xml.replace(
            '    <bpmndi:BPMNShape id="d1_t1_di"',
            '    <bpmndi:BPMNShape id="d1_stop_di" bpmnElement="d1_stop">\n'
            '      <dc:Bounds x="900.0" y="431.0" width="38.0" height="38.0"/>\n'
            '    </bpmndi:BPMNShape>\n    <bpmndi:BPMNShape id="d1_t1_di"')
        self.assertIn('id="d1_stop_di"', xml)
        resume = str(self._ecarts(xml).resume_html)
        self.assertIn("endEvent + terminateEventDefinition", resume)

    def test_compte_rendu_de_la_fusion_garde_la_trace(self):
        """Le résumé de l'analyse disparaît avec l'assistant ; la carte garde
        au chatter ce que la fusion n'a pas pu reprendre."""
        xml = self._avec_tache(self.processus.exporter_bpmn().replace(
            "Demande qualifiée", "Demande prête"))
        wiz = self._ecarts(xml)
        self.assertEqual(wiz.ecart_count, 1)
        wiz.action_appliquer()
        corps = self.processus.message_ids[0].body
        self.assertIn("Archiver la demande", corps)
        self.assertIn("ni créés ni retirés", corps)

    def test_nom_d_un_element_ecarte_reste_inerte(self):
        piege = "<img src=x onerror=alert(1)>"
        xml = self._avec_tache(
            self.processus.exporter_bpmn(),
            nom=piege.replace("<", "&lt;").replace(">", "&gt;"))
        resume = str(self._ecarts(xml).resume_html)
        self.assertNotIn("<img", resume)
        self.assertIn("onerror", resume, "le texte reste lisible, inerte")

    def test_fichier_complet_n_ecarte_rien(self):
        """Le signalement ne crie jamais sur un fichier complet.

        Ni sur son propre export, ni sur ce qu'un processus porte sans jamais
        le dessiner : documentation, extensions, objet de données.
        """
        Lecture = self.env["bf.process.lecture"]
        for p in (self.processus, self._carte_veridique()):
            for d in Lecture._lire_fichier(p.exporter_bpmn().encode("utf-8")):
                self.assertNotIn("ecartes", d, d.get("ecartes"))
        xml = self.processus.exporter_bpmn().replace(
            '<bpmn:process id="d1_process" isExecutable="false">',
            '<bpmn:process id="d1_process" isExecutable="false">\n'
            '    <bpmn:documentation>Revu en atelier.</bpmn:documentation>\n'
            '    <bpmn:extensionElements/>\n'
            '    <bpmn:dataObject id="d1_do"/>')
        self.assertIn('id="d1_do"', xml)
        d = self._lire(xml)[0]
        self.assertNotIn("ecartes", d, d.get("ecartes"))
