"""Ce que le mandat écrit sur la page : le nom du programme, la note de
confidentialité.

Ce qui compte : les deux ne sortent que si le mandat les porte, le nom du
programme se pose là où la fiche le dit, et la note est rendue ÉCHAPPÉE — elle
est écrite à la main, souvent collée d'un courriel, sur une page publique.
"""
from odoo.tests import HttpCase, TransactionCase, tagged

from .test_portail import _Mandat

PROGRAMME = "Accélération Export"
NOTE = "Les pièces de ce mandat restent entre vous et nous."


@tagged("post_install", "-at_install")
class TestTextesDuMandat(HttpCase, _Mandat):

    def setUp(self):
        super().setUp()
        self.projet, _colonnes = self._mandat(self.env)
        self.projet.action_progression_portal_publish()
        self.jeton = self.projet.progression_portal_token

    def _page(self):
        reponse = self.url_open(
            "/progression/%s/%s" % (self.projet.id, self.jeton), allow_redirects=False)
        self.assertEqual(reponse.status_code, 200)
        return reponse.text

    def test_sans_programme_ni_note_la_page_ne_dit_rien_de_plus(self):
        """Les deux blocs sont absents, pas vides : un mandat qui n'en a pas
        n'affiche ni surtitre creux ni encadré sans texte."""
        page = self._page()
        self.assertNotIn("o_progression_portal_program", page)
        self.assertNotIn("o_progression_portal_confidentiality", page)

    def test_le_programme_se_pose_au_dessus_du_titre_par_defaut(self):
        self.projet.progression_program_label = PROGRAMME
        page = self._page()
        self.assertIn(PROGRAMME, page)
        self.assertIn("o_progression_portal_program", page)
        # Le surtitre précède le titre ; c'est ce qui le rend discret.
        self.assertLess(page.index(PROGRAMME), page.index("</h1>"))

    def test_le_programme_se_pose_dans_la_ligne_du_titre_au_choix(self):
        self.projet.write({
            "progression_program_label": PROGRAMME,
            "progression_program_label_position": "inline",
        })
        page = self._page()
        self.assertIn(PROGRAMME, page)
        # Dans la ligne du titre : après le `</h1>`, et jamais dedans — le
        # libellé du titre est un terme du catalogue, et un enfant le changerait.
        self.assertGreater(page.index(PROGRAMME), page.index("</h1>"))
        self.assertNotIn(PROGRAMME, page[page.index("<h1"):page.index("</h1>")])

    def test_la_note_de_confidentialite_sort_echappee(self):
        self.projet.progression_confidentiality_note = (
            NOTE + "\n<script>alert(1)</script>")
        page = self._page()
        self.assertIn(NOTE, page)
        self.assertIn("o_progression_portal_confidentiality", page)
        # 🔴 Page PUBLIQUE : le HTML d'une note collée ne s'exécute pas.
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;", page)

    def test_la_note_porte_son_titre_dans_la_langue_du_client(self):
        """Le catalogue est SERVI, pas seulement écrit : un msgid absent du .pot
        du module est jeté en silence au chargement, et la page sortirait en
        anglais sans qu'un essai du fichier ne le voie."""
        self.env["res.lang"]._activate_lang("fr_CA")
        # Les DEUX langues, comme l'essai de langue du cœur : la bascule ne va
        # que vers une langue installée.
        self.env["res.lang"]._activate_lang("en_US")
        self.env["ir.module.module"]._load_module_terms(["bf_stepbystep_clients"], ["fr_CA"])
        self.projet.progression_confidentiality_note = NOTE
        self.projet.partner_id.lang = "fr_CA"
        self.assertIn("Confidentialité", self._page())
        self.projet.partner_id.lang = "en_US"
        self.assertIn("Confidentiality", self._page())

    def test_ni_l_un_ni_l_autre_n_ouvre_la_page_fermee(self):
        """Les deux textes ne changent rien à la garde : portail dépublié, 404."""
        self.projet.write({
            "progression_program_label": PROGRAMME,
            "progression_confidentiality_note": NOTE,
        })
        self.projet.action_progression_portal_unpublish()
        reponse = self.url_open(
            "/progression/%s/%s" % (self.projet.id, self.jeton), allow_redirects=False)
        self.assertEqual(reponse.status_code, 404)
        self.assertNotIn(PROGRAMME, reponse.text)
        self.assertNotIn(NOTE, reponse.text)


@tagged("post_install", "-at_install")
class TestTextesSurLaFiche(TransactionCase, _Mandat):

    def test_la_position_a_une_valeur_par_defaut(self):
        """Le champ est requis : un mandat sans choix pose le surtitre."""
        projet, _colonnes = self._mandat(self.env)
        self.assertEqual(projet.progression_program_label_position, "overline")

    def test_la_copie_garde_les_deux_textes(self):
        """Voulu, contrairement au jeton : un second mandat du même programme
        part de la même page. Ce qui ouvre un accès ne se copie pas, du texte si."""
        projet, _colonnes = self._mandat(self.env)
        projet.write({
            "progression_program_label": PROGRAMME,
            "progression_confidentiality_note": NOTE,
        })
        copie = projet.copy()
        self.assertEqual(copie.progression_program_label, PROGRAMME)
        self.assertEqual(copie.progression_confidentiality_note, NOTE)
        self.assertFalse(copie.progression_portal_published)
        self.assertFalse(copie.progression_portal_token)

    def test_un_projet_sans_client_ne_casse_pas_le_rendu(self):
        """`portal_client` tombe à vide sans client ; les textes du mandat, eux,
        ne dépendent pas de lui."""
        projet, _colonnes = self._mandat(self.env)
        projet.write({"partner_id": False, "progression_program_label": PROGRAMME})
        self.assertEqual(projet.progression_program_label, PROGRAMME)
