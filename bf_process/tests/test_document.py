# -*- coding: utf-8 -*-
"""Le livrable : ce qu'il porte, et ce qu'il refuse de porter.

Le contrôle central n'est pas « le PDF fait des octets » — un fichier vide en
fait aussi. C'est que **le texte des sections se retrouve dans le PDF**, et
qu'il s'y retrouve en entier : un convertisseur HTML qui perd une phrase dans
une balise qu'il ne connaît pas produit un livrable amputé, sans rien
signaler.
"""
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged

from ..generateur import texte as conv

PALETTE = {
    "title": "Vue d’ensemble", "level": "Niveau 1", "code": "d1",
    "bpmn_id": "d1", "pool": "Essai inc.",
    "col_w": 176.0, "row_h": 100.0, "lane_pad": 52.0,
    "lanes": [{"id": "op", "name": "Opérateur"}],
    "ext": [{"id": "cli", "name": "Clients", "pos": "top"}],
    "nodes": [
        {"id": "s", "kind": "start", "name": "Demande reçue", "col": 0, "row": 0},
        {"id": "a", "kind": "sub", "name": "Traiter la demande", "col": 1, "row": 0},
        {"id": "e", "kind": "end", "name": "Demande classée", "col": 2, "row": 0},
    ],
    "flows": [{"src": "s", "tgt": "a"}, {"src": "a", "tgt": "e"}],
    "msgs": [{"node": "s", "pool": "cli", "dir": "in", "label": "Demande"}],
}
ENFANT = {
    "title": "Traiter la demande", "level": "Niveau 2", "code": "d2",
    "bpmn_id": "d2", "pool": "Essai inc.",
    "col_w": 176.0, "row_h": 100.0, "lane_pad": 52.0,
    "lanes": [{"id": "op", "name": "Opérateur"}], "ext": [],
    "nodes": [
        {"id": "s", "kind": "start", "name": "Demande à traiter", "col": 0, "row": 0},
        {"id": "t", "kind": "task", "name": "Analyser la demande", "col": 1, "row": 0},
        {"id": "e", "kind": "end", "name": "Demande traitée", "col": 2, "row": 0},
    ],
    "flows": [{"src": "s", "tgt": "t"}, {"src": "t", "tgt": "e"}],
    "msgs": [],
}


@tagged("post_install", "-at_install")
class TestConversionTexte(TransactionCase):
    """Le convertisseur HTML vers objets de mise en page."""

    def test_les_blocs_gardent_leur_genre(self):
        blocs = conv.blocs("<h3>Titre</h3><p>Un paragraphe.</p>")
        self.assertEqual(blocs, [("h3", "Titre"), ("p", "Un paragraphe.")])

    def test_une_liste_a_puces_rend_des_puces(self):
        blocs = conv.blocs("<ul><li>Premier</li><li>Second</li></ul>")
        self.assertEqual([g for g, _t in blocs], ["puce", "puce"])

    def test_une_liste_numerotee_porte_son_rang(self):
        blocs = conv.blocs("<ol><li>Premier</li><li>Second</li></ol>")
        self.assertEqual(blocs, [("numero", "1. Premier"), ("numero", "2. Second")])

    def test_le_gras_survit_a_la_conversion(self):
        blocs = conv.blocs("<p>Du <strong>gras</strong> et de l’<em>italique</em>.</p>")
        self.assertEqual(blocs[0][1], "Du <b>gras</b> et de l’<i>italique</i>.")

    def test_une_balise_inconnue_ne_mange_pas_son_texte(self):
        """Perdre une phrase en silence est le pire comportement possible.

        Une balise que le convertisseur ne connaît pas voit son contenu
        conservé : on préfère un bloc mal classé à une phrase disparue.

        ⚠️ Le texte est NU dans la balise inconnue, sans `<p>` intérieur.
        La première version de ce test enveloppait la phrase dans un `<p>`,
        qui la sauvait quoi qu'il arrive : le test passait au vert sur un
        convertisseur qui perdait bel et bien le texte hors bloc. Trouvé en
        cassant le code exprès, pas en le relisant.
        """
        blocs = conv.blocs("<section>Une phrase qui compte.</section>")
        self.assertIn("Une phrase qui compte.", [t for _g, t in blocs])
        # et le cas mixte : du texte nu AVANT un bloc reconnu
        blocs = conv.blocs("Texte nu en tête.<p>Puis un paragraphe.</p>")
        tout = " ".join(t for _g, t in blocs)
        self.assertIn("Texte nu en tête.", tout)
        self.assertIn("Puis un paragraphe.", tout)

    def test_le_html_est_echappe(self):
        """Un texte saisi par un humain ne doit pas casser la mise en page."""
        blocs = conv.blocs("<p>Le seuil est &lt; 5 &amp; &gt; 2</p>")
        self.assertIn("&lt;", blocs[0][1])
        self.assertIn("&amp;", blocs[0][1])

    def test_un_balisage_bancal_ne_derape_pas(self):
        """Une balise mal refermée ne doit pas déclasser tout ce qui suit."""
        blocs = conv.blocs("<p>Ouvert <b>gras</p><p>Suite normale.</p>")
        self.assertEqual([g for g, _t in blocs], ["p", "p"])

    def test_une_entree_vide_rend_une_liste_vide(self):
        for vide in (None, "", "   "):
            self.assertEqual(conv.blocs(vide), [], repr(vide))

    def test_texte_nu_retire_toutes_les_balises(self):
        nu = conv.texte_nu("<p>Du <b>gras</b> ici.</p>")
        self.assertNotIn("<", nu)
        self.assertIn("gras", nu)


@tagged("post_install", "-at_install")
class TestDocument(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.groups_id |= cls.env.ref("bf_process.group_bf_process_manager")
        Process = cls.env["bf.process"]
        cls.client = cls.env["res.partner"].create({"name": "Client d’essai inc."})
        cls.carte = Process.browse(Process.charger_dicts(
            {"name": "Traiter une demande", "code": "DOC", "version": "1.0",
             "pool_name": PALETTE["pool"], "partner_id": cls.client.id,
             "sous_titre": "État actuel, de la demande au classement",
             "source": "Reconstruit du verbatim.\nDeuxième ligne ignorée."},
            [PALETTE, ENFANT]))
        Section = cls.env["bf.process.section"]
        cls.section = Section.create({
            "process_id": cls.carte.id, "kind": "hypothese",
            "name": "Un seul couloir",
            "body": "<p>La comptabilité est portée par une seule personne.</p>"})
        Section.create([
            {"process_id": cls.carte.id, "kind": "couverture",
             "name": "Ce que contient le document",
             "body": "<p>Le cycle complet, en notation BPMN 2.0.</p>"},
            {"process_id": cls.carte.id, "kind": "question",
             "name": "Date de fin d’exercice",
             "body": "<p>Le 30 ou le 31 août ?</p>"},
            {"process_id": cls.carte.id, "kind": "constat",
             "name": "Un seul exécutant",
             "body": "<p>Il n’y a ni relève ni second regard.</p>"},
            {"process_id": cls.carte.id, "kind": "validation",
             "name": "Propriétaire du processus", "body": "<p>Jean Tremblay</p>"},
            {"process_id": cls.carte.id, "kind": "annexe",
             "name": "Le rattrapage convenu",
             "body": "<h3>Ce qui a été convenu</h3><ol><li>Numériser les pièces.</li></ol>"},
        ])

    def _texte(self, octets):
        """Le texte du PDF, page par page.

        Lu avec `pdfminer`, présent dans l'image Odoo — et non avec PyMuPDF,
        qui sert hors serveur mais n'est pas installé ici. Un test qui ne
        peut pas tourner là où le code tourne ne protège rien.
        """
        import io as _io
        from pdfminer.high_level import extract_text
        pages, i = [], 0
        while True:
            try:
                t = extract_text(_io.BytesIO(octets), page_numbers=[i])
            except Exception:
                break
            if not t and i:
                break
            pages.append(t)
            i += 1
            if i > 60:          # garde-fou : un document ne fait pas 60 pages
                break
        return pages

    # ------------------------------------------------------------ le modèle
    def test_une_section_vide_est_refusee(self):
        """Une section vide se numérote et se lit comme une omission."""
        with self.assertRaises(ValidationError):
            self.env["bf.process.section"].create({
                "process_id": self.carte.id, "kind": "hypothese",
                "name": "Sans corps", "body": ""})

    def test_le_registre_accepte_d_arriver_vide(self):
        """Sa raison d'être est d'être rempli à la main, plus tard."""
        ligne = self.env["bf.process.section"].create({
            "process_id": self.carte.id, "kind": "validation",
            "name": "Exécutant du processus", "body": ""})
        self.assertTrue(ligne.id)

    def test_le_pied_retombe_sur_la_premiere_ligne_de_source(self):
        self.assertEqual(self.carte._pied(), "Reconstruit du verbatim.")

    def test_le_pied_explicite_prime_sur_la_source(self):
        self.carte.pied_document = "Aucune observation sur place."
        self.assertEqual(self.carte._pied(), "Aucune observation sur place.")
        self.carte.pied_document = False

    def test_les_sections_se_gelent_avec_la_version(self):
        """Une carte citée ne voit pas ses hypothèses changer sous elle."""
        self.carte.state = "valide"
        try:
            with self.assertRaises(Exception):
                self.section.write({"name": "Modifié après le gel"})
        finally:
            self.carte.state = "brouillon"

    # ---------------------------------------------------------- le document
    def test_le_document_porte_le_texte_des_sections(self):
        """Le contrôle central : la prose se retrouve dans le PDF."""
        pages = self._texte(self.carte._document_octets())
        tout = " ".join(pages).replace("\n", " ")
        for attendu in ("Un seul couloir",
                        "portée par une seule personne",
                        "Date de fin d’exercice",
                        "Un seul exécutant",
                        "ni relève ni second regard",
                        "Le rattrapage convenu",
                        "Numériser les pièces"):
            self.assertIn(attendu, tout, attendu)

    def test_le_document_a_couverture_niveaux_annexes_et_depliee(self):
        octets = self.carte._document_octets()
        pages = self._texte(octets)
        # couverture + 2 niveaux + hypothèses/questions + constats + annexe
        # + carte dépliée
        self.assertEqual(len(pages), 7)
        self.assertIn("Sommaire", pages[0])
        self.assertIn("Légende", pages[0])
        self.assertIn("Hypothèses et questions ouvertes", pages[3])
        self.assertIn("Constats et pistes", pages[4])

    def test_le_sommaire_dit_la_bonne_page(self):
        """Un sommaire qui ment est pire qu'aucun sommaire."""
        pages = self._texte(self.carte._document_octets())
        import re
        sommaire = pages[0]
        for titre in ("Traiter la demande", "Constats et pistes"):
            self.assertIn(titre, sommaire, titre)
        # le titre annoncé en page N doit se trouver EN page N
        for m in re.finditer(r"(\d+)\s+(Hypothèses et questions ouvertes|Constats et pistes)",
                             sommaire.replace("\n", " ")):
            no, titre = int(m.group(1)), m.group(2)
            self.assertIn(titre, pages[no - 1], "%s annoncé p.%s" % (titre, no))

    def test_le_registre_arrive_dans_le_document(self):
        pages = self._texte(self.carte._document_octets())
        tout = " ".join(pages)
        self.assertIn("Registre de validation", tout)
        self.assertIn("Jean Tremblay", tout)
        self.assertIn("Conforme", tout)

    def test_la_legende_ne_montre_que_les_formes_presentes(self):
        """Une légende qui explique une forme absente fait douter le lecteur."""
        couverture = self._texte(self.carte._document_octets())[0]
        self.assertIn("Sous-processus", couverture)      # la carte en a un
        self.assertNotIn("Dépôt de données", couverture)  # la carte n'en a pas
        self.assertNotIn("Parallélisme", couverture)

    def test_l_etat_est_dit_au_lecteur_pas_en_jargon(self):
        """« Brouillon » se lirait comme un défaut de la carte."""
        couverture = self._texte(self.carte._document_octets())[0]
        self.assertIn("projet, à valider", couverture)
        self.assertNotIn("Brouillon", couverture)

    def test_une_carte_sans_niveau_refuse_lisiblement(self):
        vide = self.env["bf.process"].create({
            "name": "Carte sans niveau", "version": "1.0", "pool_name": "X"})
        from odoo.exceptions import UserError
        with self.assertRaises(UserError):
            vide._document_octets()

    def test_le_document_ne_touche_pas_au_trace_nu(self):
        """Le tracé nu reste ce qu'il était : le document s'ajoute, il ne remplace pas."""
        nu = self._texte(self.carte._pdf_octets())
        self.assertEqual(len(nu), 2)                 # une page par niveau
        self.assertNotIn("Sommaire", " ".join(nu))
