# -*- coding: utf-8 -*-
"""Chaque texte de la cartographie sort dans la langue de qui le lira.

La source du module est en anglais ; le français vit dans `i18n/fr_CA.po`.
Ces essais prouvent que :

* le livrable PDF, le tracé, la vue delta et le tirage d'atelier s'impriment
  dans la langue du client de la carte, jamais dans celle de qui imprime ;
* les libellés d'écarts semés, les noms par défaut des nœuds, la source et le
  titre d'une carte importée sont écrits dans la langue du client, parce
  qu'ils finissent dans ses documents ;
* la description d'une tâche tirée d'un écart suit la langue de son
  responsable ;
* les notes au chatter gardent leur mise en forme au lieu d'être échappées ;
* les résultats d'assistant, les refus de lecture et de fusion, la palette et
  le nom affiché suivent la langue de la session ;
* la page publique d'une étape suit la langue du visiteur.

Deux langues comptent donc, et elles ne sont pas la même :

* **celle du client de la carte** (`bf.process._langue_livrable`) pour tout ce
  qui part chez lui ou s'imprime : habillage des PDF, libellés d'écarts semés
  (ils vont au plan de transformation), noms par défaut des nœuds, source
  d'une carte importée, titre d'un niveau sans nom ;
* **celle de la session** pour ce que voit qui agit : résultats d'assistant,
  compte rendu de fusion, palette, nom affiché.

Chaque essai fait diverger les deux exprès (client fr_CA, session en_CA, ou
l'inverse) : un texte rendu dans la mauvaise langue tombe, alors qu'il passerait
inaperçu si les deux étaient égales. Les langues sont épinglées sur les
partenaires créés ici : rien ne dépend des données copiées de la prod.
"""
import base64
import io
import re

from odoo.tests import HttpCase, TransactionCase, tagged
from odoo.tests.common import new_test_user

from .test_aller_retour import CARTE

FR, EN = "fr_CA", "en_CA"


def _texte_pdf(octets):
    """Le texte du PDF entier, lu par `pdfminer` comme les autres essais."""
    from pdfminer.high_level import extract_text
    return extract_text(io.BytesIO(octets))


class _Base(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for code in (FR, EN):
            cls.env["res.lang"]._activate_lang(code)
        cls.env["ir.module.module"]._load_module_terms(
            ["bf_process"], [FR], overwrite=True)
        cls.client_fr = cls.env["res.partner"].create(
            {"name": "Client francophone inc.", "lang": FR})
        cls.client_en = cls.env["res.partner"].create(
            {"name": "English Client Ltd.", "lang": EN})

    _numero = 0

    def _carte(self, client, session):
        """Une carte de l'état actuel, rendue dans la langue de la session.

        Le nom est numéroté : (nom, nature, version) est unique.
        """
        _Base._numero += 1
        p = self.env["bf.process"].create({
            "name": "Traiter une demande %d" % _Base._numero, "code": "dem",
            "pool_name": "Essai inc.",
            "partner_id": client.id if client else False})
        p._charger_niveaux(CARTE)
        return p.with_context(lang=session)

    def _cible(self, actuel):
        cible = self.env["bf.process"].browse(
            actuel.action_dessiner_cible()["res_id"])
        return cible.with_context(lang=actuel.env.lang)

    def _semer_avec_changements(self, cible):
        """Renomme t1 et en fait une tâche humaine, change le ton de
        l'annotation, puis sème.

        Aucun flux ne change ici : les écarts de flux ont leur propre essai
        (`test_ecarts_de_flux_dans_la_langue_du_client`), qui garde aussi le
        nom du paramètre `%(depart)s` (`%(source)s` heurtait le premier
        paramètre d'`Environment._()` et faisait tomber le semis).
        """
        par_code = {n.code: n for n in cible.diagram_ids.node_ids}
        par_code["t1"].write({"name": "Qualifier vite", "kind": "user"})
        par_code["n1"].tone = "ai"
        cible.action_semer_ecarts()
        return {e.genre: e for e in cible.ecart_ids}


# ======================================================== 1. langue du livrable
@tagged("post_install", "-at_install")
class TestLangueDuLivrable(_Base):

    def test_la_langue_du_livrable_est_celle_du_client_pas_de_la_session(self):
        """La langue du livrable est celle du client de la carte."""
        self.assertEqual(self._carte(self.client_fr, EN)._langue_livrable(), FR)
        self.assertEqual(self._carte(self.client_en, FR)._langue_livrable(), EN)

    def test_sans_client_la_langue_de_la_societe_prime_sur_la_session(self):
        """Sans client, la langue de la société, jamais celle de la session."""
        self.env.company.partner_id.lang = FR
        self.assertEqual(self._carte(False, EN)._langue_livrable(), FR)


# =================================================================== 2. PDF
@tagged("post_install", "-at_install")
class TestPdfDansLaLangueDuClient(_Base):

    def test_livrable_client_francais_imprime_par_un_anglophone(self):
        """Couverture, légende, sommaire et bandeau suivent le client."""
        carte = self._carte(self.client_fr, EN)
        texte = _texte_pdf(carte._document_octets())
        for attendu in ("Sommaire", "Légende", "projet, à valider",
                        "Cartographie des processus (AS-IS)"):
            self.assertIn(attendu, texte, attendu)
        for absent in ("Contents", "Legend", "draft, to be validated",
                       "Process map (AS-IS)"):
            self.assertNotIn(absent, texte, absent)

    def test_livrable_client_anglais_imprime_par_un_francophone(self):
        """Et l'inverse, sans espacement français dans un livrable anglais."""
        carte = self._carte(self.client_en, FR)
        self.env["bf.process.section"].create({
            "process_id": carte.id, "kind": "couverture",
            "name": "Scope", "body": "<p>Is the year-end date known?</p>"})
        texte = _texte_pdf(carte._document_octets())
        for attendu in ("Contents", "Legend", "draft, to be validated",
                        "Process map (AS-IS)"):
            self.assertIn(attendu, texte, attendu)
        self.assertNotIn("Sommaire", texte)
        # aucune insécable posée devant le point d'interrogation en anglais
        self.assertIn("known?", texte)

    def test_plan_de_transformation_dans_la_langue_du_client(self):
        """Le plan de transformation lit intention, état et effort sur les
        sélections, dans la langue du client."""
        cible = self._cible(self._carte(self.client_fr, EN))
        self._semer_avec_changements(cible)
        cible.ecart_ids[0].write({"intention": "automatiser",
                                  "effort": "faible"})
        ligne = [l for l in cible._document_plan() if l["intention"]][0]
        self.assertEqual(ligne["intention"], "Automatiser")
        self.assertEqual(ligne["effort"], "Faible")
        self.assertEqual(ligne["etat"], "À décider")

    def test_vue_delta_dans_la_langue_du_client(self):
        """Étiquettes, légende et titre de la vue delta suivent le client."""
        cible = self._cible(self._carte(self.client_fr, EN))
        self._semer_avec_changements(cible)
        pages = cible._pages_delta()
        self.assertTrue(pages[0]["level"].startswith("État actuel"))
        self.assertTrue(pages[-1]["level"].startswith("Processus souhaité"))
        texte = _texte_pdf(cible._pdf_delta_octets())
        self.assertIn("Vue delta", texte)
        self.assertIn("1.0 vers 1.0", texte)
        self.assertNotIn("Delta view", texte)

    def test_tirage_d_atelier_dans_la_langue_du_client(self):
        """Le repère d'assemblage des tuiles du tirage d'atelier suit le client."""
        carte = self._carte(self.client_fr, EN)
        noeud = carte.diagram_ids.node_ids.filtered(lambda n: n.code == "t1")
        self.env["bf.process.node.resource"].create({
            "node_id": noeud.id, "name": "Comment souder", "kind": "video",
            "url": "https://exemple.test/video.mp4"})
        action = carte.action_telecharger_pdf_atelier()
        piece = self.env["ir.attachment"].browse(
            int(re.search(r"/web/content/(\d+)", action["url"]).group(1)))
        texte = _texte_pdf(piece.raw)
        self.assertIn("Colonne 1/", texte)
        self.assertNotIn("Column 1/", texte)


# ===================================================== 3. libellés d'écarts
@tagged("post_install", "-at_install")
class TestEcartsDansLaLangueDuClient(_Base):

    def test_libelles_semes_dans_la_langue_du_client(self):
        """Les libellés d'écarts semés se composent dans la langue du client,
        avec les genres et les tons lus sur les sélections du nœud."""
        ecarts = self._semer_avec_changements(
            self._cible(self._carte(self.client_fr, EN)))
        self.assertIn("vers « Qualifier vite » dans « ",
                      ecarts["renommage"].libelle)
        self.assertTrue(ecarts["nature"].libelle.startswith(
            "« Qualifier vite » : tâche vers tâche humaine dans « "),
            ecarts["nature"].libelle)
        self.assertIn("fragilité ou information manquante vers piste "
                      "d'amélioration", ecarts["ton"].libelle)

    def test_ecarts_de_flux_dans_la_langue_du_client(self):
        """Ajout, retrait et porte d'un flux, en français pour un
        client francophone semé par un anglophone. Un flux redirigé fait un
        retrait et un ajout ; une porte effacée se dit « sans porte »."""
        cible = self._cible(self._carte(self.client_fr, EN))
        flux = {(f.source_id.code, f.target_id.code): f
                for f in cible.diagram_ids.flow_ids}
        par_code = {n.code: n for n in cible.diagram_ids.node_ids}
        flux[("t1", "e")].target_id = par_code["e2"]
        flux[("g", "e2")].label = False
        cible.action_semer_ecarts()
        ecarts = {e.genre: e.libelle for e in cible.ecart_ids
                  if e.portee == "flux"}
        self.assertEqual(ecarts["ajout"],
                         "Qualifier la demande vers Demande écartée")
        self.assertEqual(ecarts["retrait"],
                         "Qualifier la demande vers Demande qualifiée")
        self.assertEqual(ecarts["porte"],
                         "Demande recevable ? vers Demande écartée : "
                         "« Non » vers « sans porte »")

    def test_libelles_semes_pour_un_client_anglais(self):
        """Et en anglais pour un client anglais, même semé par
        un francophone."""
        ecarts = self._semer_avec_changements(
            self._cible(self._carte(self.client_en, FR)))
        self.assertTrue(ecarts["nature"].libelle.startswith(
            '"Qualifier vite": task to user task in "'), ecarts["nature"].libelle)
        self.assertIn("weak point or missing information to improvement idea",
                      ecarts["ton"].libelle)
        self.assertNotIn("«", ecarts["renommage"].libelle)


# ================================================================= 4. tâche
@tagged("post_install", "-at_install")
class TestTacheDUnEcart(_Base):

    def _ecart_avec_projet(self, client, session):
        cible = self._cible(self._carte(client, session))
        cible.project_id = self.env["project.project"].create(
            {"name": "Transformation"})
        par_code = {n.code: n for n in cible.diagram_ids.node_ids}
        par_code["t1"].name = "Qualifier automatiquement"
        cible.action_semer_ecarts()
        ecart = cible.ecart_ids
        ecart.write({"intention": "automatiser", "gain": "Deux heures"})
        return ecart

    def test_description_dans_la_langue_du_responsable(self):
        """Le responsable francophone lit sa tâche en français,
        quel que soit le client ou qui la crée ; un paragraphe par ligne."""
        ecart = self._ecart_avec_projet(self.client_en, EN)
        ecart.responsable_id = new_test_user(
            self.env, login="essai_fr", lang=FR, groups="base.group_user")
        ecart.action_creer_tache()
        description = str(ecart.task_id.description)
        self.assertIn("Écart relevé entre", description)
        self.assertIn("Automatiser", description)
        self.assertNotIn("Gap found", description)
        self.assertIn("</p><p>", description.replace("\n", ""))

    def test_sans_responsable_la_langue_du_client(self):
        """Sans responsable, la description prend la langue du client."""
        ecart = self._ecart_avec_projet(self.client_fr, EN)
        ecart.action_creer_tache()
        self.assertIn("Écart relevé entre", str(ecart.task_id.description))


# =============================================================== 5. chatter
@tagged("post_install", "-at_install")
class TestNotesAuChatter(_Base):

    def test_le_gras_des_notes_n_est_pas_echappe(self):
        """Les notes au chatter gardent leur gras : `Markup(_(…)) %`, sinon
        Odoo 18 échappe les balises."""
        carte = self._carte(self.client_fr, FR)
        cible = self._cible(carte)
        self.assertIn("<b>", carte.message_ids[0].body)
        self.assertIn("<b>", cible.message_ids[0].body)
        self._semer_avec_changements(cible)
        self.assertIn("<b>", cible.message_ids[0].body)
        self.assertNotIn("&lt;b&gt;", cible.message_ids[0].body)
        cible.action_valider()
        self.assertIn("<b>1.0</b>", cible.message_ids[0].body)


# ==================================================== 6. noms par défaut
@tagged("post_install", "-at_install")
class TestNomsParDefaut(_Base):

    def _centre_t1(self, niveau):
        n = [x for x in niveau.rendu()["noeuds"] if x["id"] == "t1"][0]
        return n["x"] + n["w"] / 2, n["y"] + n["h"] / 2

    def test_noeud_pose_depuis_le_trace(self):
        """Le nom par défaut d'un nœud posé s'imprime : langue du client."""
        niveau = self._carte(self.client_fr, EN).diagram_ids
        cx, cy = self._centre_t1(niveau)
        niveau.creer_noeud("task", cx, cy + 60)
        niveau.creer_noeud("note", cx, cy + 120)
        neufs = niveau.node_ids.filtered(lambda n: n.code in ("n2", "n3"))
        self.assertEqual(sorted(neufs.mapped("name")), ["À nommer", "À écrire"])

    def test_noeud_cree_par_la_fusion(self):
        """Même règle pour une activité sans nom arrivée par fusion."""
        carte = self._carte(self.client_fr, EN)
        xml = carte.exporter_bpmn().replace(
            '<bpmn:task id="d1_t1" name="Qualifier la demande">',
            '<bpmn:task id="d1_t9" name=""/>\n'
            '    <bpmn:task id="d1_t1" name="Qualifier la demande">')
        xml = xml.replace(
            '<bpmndi:BPMNShape id="d1_t1_di" bpmnElement="d1_t1">',
            '<bpmndi:BPMNShape id="d1_t9_di" bpmnElement="d1_t9">\n'
            '      <dc:Bounds x="700.0" y="400.0" width="152.0" height="68.0"/>\n'
            '    </bpmndi:BPMNShape>\n'
            '    <bpmndi:BPMNShape id="d1_t1_di" bpmnElement="d1_t1">')
        wiz = _assistant_fusion(carte, xml)
        wiz.action_analyser()
        wiz.action_appliquer()
        cree = carte.diagram_ids.node_ids.filtered(lambda n: n.code == "t9")
        self.assertEqual(cree.name, "À nommer")


def _assistant_fusion(carte, xml, nom="retouche.bpmn"):
    return carte.env["bf.process.merge.wizard"].create({
        "process_id": carte.id, "nom_fichier": nom,
        "fichier": base64.b64encode(xml.encode("utf-8"))})


def _avec_tache_sans_forme(xml):
    """Ajoute une tâche sans forme DI : elle sera écartée à la lecture."""
    t1 = '<bpmn:task id="d1_t1" name="Qualifier la demande">'
    assert t1 in xml
    return xml.replace(t1, '<bpmn:task id="d1_t9" name="Archiver la demande"/>'
                           '\n    ' + t1)


def _sans_forme(xml, ident):
    xml, n = re.subn(
        r'    <bpmndi:BPMNShape id="[^"]*" bpmnElement="%s"[^>]*>.*?'
        r'</bpmndi:BPMNShape>\n' % re.escape(ident), "", xml, flags=re.S)
    assert n == 1, ident
    return xml


# =========================================================== 7 et 8. import
@tagged("post_install", "-at_install")
class TestImport(_Base):

    def _importer(self, xml, client, session, nom="partiel.bpmn"):
        wiz = self.env["bf.process.import.wizard"].with_context(
            lang=session).create({
                "name": "Import d'essai %s %s" % (session, client.lang),
                "nom_fichier": nom,
                "partner_id": client.id,
                "fichier": base64.b64encode(xml.encode("utf-8"))})
        return self.env["bf.process"].browse(wiz.action_importer()["res_id"])

    def test_source_et_titre_importe_dans_la_langue_du_client(self):
        """La source (pied du PDF) et le titre d'un niveau sans nom d'une
        carte importée sont écrits dans la langue du client."""
        xml = self._carte(self.client_fr, EN).exporter_bpmn()
        xml, n = re.subn(r'(<bpmndi:BPMNDiagram id="[^"]*") name="[^"]*"',
                         r"\1", xml)
        self.assertEqual(n, 1)
        p = self._importer(xml, self.client_fr, EN, nom=False)
        self.assertEqual(p.source, "Importé du fichier BPMN 2.0 « sans nom ».")
        self.assertEqual(p.diagram_ids.title, "Niveau importé")

    def test_elements_ecartes_nommes_dans_la_langue_de_la_session(self):
        """Les éléments écartés à la lecture se nomment dans la langue de la
        session : genre lu sur la sélection, motif traduit."""
        xml = _avec_tache_sans_forme(
            self._carte(self.client_fr, EN).exporter_bpmn())
        corps_fr = str(self._importer(xml, self.client_en, FR).message_ids[0].body)
        self.assertIn("tâche « Archiver la demande »", corps_fr)
        corps_en = str(self._importer(xml, self.client_fr, EN).message_ids[0].body)
        self.assertIn("Archiver la demande", corps_en)
        self.assertIn("task", corps_en)
        self.assertNotIn("«", corps_en)

    def test_refus_de_lecture_dans_la_langue_de_la_session(self):
        """Une forme sans bornes se refuse dans la langue de qui importe, même
        hors requête HTTP (XML-RPC, essai) : la méthode statique reçoit l'env."""
        carte = self._carte(self.client_en, EN)
        xml, n = re.subn(r'(<bpmndi:BPMNShape id="d1_t1_di"[^>]*>)\s*<dc:Bounds[^>]*/>',
                         r"\1", carte.exporter_bpmn())
        self.assertEqual(n, 1)
        Lecture = self.env["bf.process.lecture"]
        with self.assertRaisesRegex(Exception, "n'a pas de <dc:Bounds>"):
            Lecture.with_context(lang=FR)._lire_fichier(xml.encode("utf-8"))
        with self.assertRaisesRegex(Exception, "has no <dc:Bounds>"):
            Lecture.with_context(lang=EN)._lire_fichier(xml.encode("utf-8"))

    def test_refus_de_fusion_dans_la_langue_de_la_session(self):
        """Un fichier sans niveau commun se refuse dans la langue de la
        session, même hors requête HTTP."""
        carte = self._carte(self.client_en, FR)
        xml = carte.exporter_bpmn().replace("d1_", "zz1_")
        wiz = _assistant_fusion(carte, xml)
        with self.assertRaisesRegex(Exception, "Aucun niveau de ce fichier"):
            wiz.action_analyser()

    def test_couloir_ecarte_dit_lane_en_anglais(self):
        """Un couloir écarté se dit « lane » en anglais, pas « couloir »."""
        carte = self._carte(self.client_fr, EN)
        wiz = _assistant_fusion(carte, _sans_forme(carte.exporter_bpmn(),
                                                   "d1_lane_f"))
        wiz.action_analyser()
        resume = str(wiz.resume_html)
        self.assertIn("lane", resume)
        self.assertIn("Conseil", resume)
        self.assertNotIn("couloir", resume)


# ================================================================= 9. fusion
@tagged("post_install", "-at_install")
class TestFusionDansLaLangueDeLaSession(_Base):

    def _fusion(self, session):
        carte = self._carte(self.client_en, session)
        xml = carte.exporter_bpmn().replace("Demande qualifiée", "Demande prête")
        wiz = _assistant_fusion(carte, xml)
        wiz.action_analyser()
        return carte, wiz

    def test_resume_et_compte_rendu_en_francais(self):
        """Résumé et compte rendu de fusion passent par le catalogue."""
        carte, wiz = self._fusion(FR)
        self.assertIn("page(s) lue(s)", str(wiz.resume_html))
        wiz.action_appliquer()
        self.assertIn("Fusion de", str(carte.message_ids[0].body))

    def test_resume_et_compte_rendu_en_anglais(self):
        """Et plus aucun français pour un anglophone."""
        carte, wiz = self._fusion(EN)
        self.assertIn("page(s) read", str(wiz.resume_html))
        self.assertNotIn("lue(s)", str(wiz.resume_html))
        wiz.action_appliquer()
        corps = str(carte.message_ids[0].body)
        self.assertIn("Merge of", corps)
        self.assertNotIn("Fusion de", corps)

    def test_genre_d_un_noeud_neuf_lu_sur_la_selection(self):
        """Le genre d'un nœud neuf se lit sur la sélection traduite."""
        carte = self._carte(self.client_en, FR)
        xml = carte.exporter_bpmn().replace(
            '<bpmn:task id="d1_t1" name="Qualifier la demande">',
            '<bpmn:task id="d1_t9" name="Consigner"/>\n'
            '    <bpmn:task id="d1_t1" name="Qualifier la demande">').replace(
            '<bpmndi:BPMNShape id="d1_t1_di" bpmnElement="d1_t1">',
            '<bpmndi:BPMNShape id="d1_t9_di" bpmnElement="d1_t9">\n'
            '      <dc:Bounds x="700.0" y="400.0" width="152.0" height="68.0"/>\n'
            '    </bpmndi:BPMNShape>\n'
            '    <bpmndi:BPMNShape id="d1_t1_di" bpmnElement="d1_t1">')
        wiz = _assistant_fusion(carte, xml)
        wiz.action_analyser()
        ajout = wiz.line_ids.filtered(
            lambda l: l.portee == "noeud" and l.operation == "ajout")
        self.assertIn("tâche « Consigner »", ajout.libelle)


# ======================================================== 10. comparaison
@tagged("post_install", "-at_install")
class TestComparaison(_Base):

    def test_comparaison_dans_la_langue_de_la_session(self):
        """L'assistant de comparaison traduit titres de bloc, genres et en-tête."""
        actuel = self._carte(self.client_en, FR)
        cible = self._cible(actuel)
        cible.diagram_ids.node_ids.filtered(
            lambda n: n.code == "t1").name = "Qualifier vite"
        wiz = self.env["bf.process.compare.wizard"].with_context(
            lang=FR).create({"source_id": cible.id, "cible_id": actuel.id})
        wiz.action_comparer()
        resume = str(wiz.rapport_html)
        self.assertIn("Nœuds", resume)
        self.assertIn("Renommage", resume)
        self.assertIn("De <b>", resume)
        self.assertNotIn("Nodes", resume)


# ================================================== 12 et 13. écran et nom
@tagged("post_install", "-at_install")
class TestEcran(_Base):

    def test_palette_dans_la_langue_de_la_session(self):
        """La palette de l'éditeur lit la sélection traduite."""
        niveau = self._carte(self.client_en, FR).diagram_ids
        palette = {g["code"]: g["nom"] for g in niveau.rendu()["palette"]}
        self.assertEqual(palette["task"], "Tâche")

    def test_nom_affiche_suit_la_langue(self):
        """Le suffixe du nom affiché se traduit, et se recalcule quand la langue change
        (`depends_context`) au lieu de garder la première lecture en cache."""
        cible = self._cible(self._carte(self.client_en, EN))
        self.assertTrue(cible.with_context(lang=EN).display_name.endswith(
            "(target)"))
        self.assertTrue(cible.with_context(lang=FR).display_name.endswith(
            "(souhaité)"))


# ================================================================ 11. portail
@tagged("post_install", "-at_install")
class TestPortailDansLaLangueDuVisiteur(HttpCase):

    def setUp(self):
        super().setUp()
        if "website" not in self.env:
            self.skipTest("route du portail servie par le site")
        langues = self.env["res.lang"]
        for code in (FR, EN):
            langues |= self.env["res.lang"]._activate_lang(code)
        self.env["ir.module.module"]._load_module_terms(
            ["bf_process"], [FR], overwrite=True)
        site = self.env["website"].get_current_website()
        site.language_ids |= langues
        processus = self.env["bf.process"].create({
            "name": "Essai portail", "code": "essai", "pool_name": "Essai inc."})
        processus._charger_niveaux(CARTE)
        self.noeud = processus.diagram_ids.node_ids.filtered(
            lambda n: n.code == "t1")
        self.env["bf.process.node.resource"].create({
            "node_id": self.noeud.id, "name": "Fiche du décapant",
            "kind": "fiche", "url": "https://exemple.test/sds.pdf"})
        self.noeud._portal_ensure_token()

    def _page(self, code):
        prefixe = self.env["res.lang"]._lang_get(code).url_code
        return self.url_open("/%s/carte/etape/%s?access_token=%s" % (
            prefixe, self.noeud.id, self.noeud.access_token)).text

    def test_genre_de_ressource_dans_la_langue_de_la_page(self):
        """La page française dit « Fiche signalétique »,
        l'anglaise « Safety data sheet »."""
        self.assertIn("Fiche signalétique", self._page(FR))
        page_en = self._page(EN)
        self.assertIn("Safety data sheet", page_en)
        self.assertNotIn("Fiche signalétique", page_en)
