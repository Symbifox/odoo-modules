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
