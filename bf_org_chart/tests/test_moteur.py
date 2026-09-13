# -*- coding: utf-8 -*-
"""Le moteur se teste sans base : une carte entre, un plan sort."""
import xml.etree.ElementTree as ET

from odoo.tests import TransactionCase, tagged

from ..moteur import disposition as dsp
from ..moteur import modele, pdf, svg


def _carte(boites, aretes, **kw):
    c = modele.Carte(**kw)
    for cle in boites:
        c.boites.append(modele.Boite(cle=cle, titre=cle.upper(), sous_titre="Rôle"))
    for de, vers, *reste in aretes:
        c.aretes.append(modele.Arete(de=de, vers=vers,
                                     etiquette=reste[0] if reste else ""))
    return c


@tagged("post_install", "-at_install", "bf_org_chart")
class TestMoteur(TransactionCase):

    def test_arbre_quand_un_seul_parent(self):
        plan = dsp.disposer(_carte(["a", "b", "c"], [("a", "b"), ("a", "c")]))
        self.assertEqual(plan.mode, "arbre")

    def test_parent_centre_sur_ses_enfants(self):
        plan = dsp.disposer(_carte(["a", "b", "c"], [("a", "b"), ("a", "c")]))
        a, b, c = (plan.boite(k) for k in ("a", "b", "c"))
        self.assertAlmostEqual(a.cx, (b.cx + c.cx) / 2.0, places=1)
        self.assertLess(a.y, b.y, "le parent est au-dessus de ses enfants")

    def test_couches_des_qu_une_boite_a_deux_parents(self):
        plan = dsp.disposer(_carte(["h1", "h2", "f"], [("h1", "f"), ("h2", "f")]))
        self.assertEqual(plan.mode, "couches")
        self.assertEqual(len(plan.aretes), 2)

    def test_boucle_ecartee_sans_lever(self):
        plan = dsp.disposer(_carte(["a", "b", "c"],
                                   [("a", "b"), ("b", "c"), ("c", "a")]))
        self.assertEqual(len(plan.avertissements), 1)
        self.assertEqual(len(plan.aretes), 2, "l'arête de retour ne se dessine pas")

    def test_arete_vers_une_boite_absente_est_ecartee(self):
        carte = _carte(["a"], [])
        carte.aretes.append(modele.Arete(de="a", vers="fantome"))
        plan = dsp.disposer(carte)
        self.assertEqual(plan.aretes, [])

    def test_carte_vide_rend_une_page(self):
        plan = dsp.disposer(modele.Carte(titre="Rien"))
        self.assertGreater(plan.largeur, 0)
        self.assertEqual(plan.boites, [])

    def test_saut_de_niveau_passe_par_un_couloir(self):
        """Une arête qui saute un niveau ne traverse plus la boîte du milieu."""
        carte = _carte(["h", "m", "b"], [("h", "m"), ("m", "b"), ("h", "b")])
        plan = dsp.disposer(carte)
        longue = [a for a in plan.aretes if a.de == "h" and a.vers == "b"][0]
        self.assertGreaterEqual(len(longue.points), 4,
                                "le tracé fait un détour, il ne coupe pas tout droit")
        milieu = plan.boite("m")
        for x, y in longue.points:
            dedans = (milieu.x < x < milieu.x + milieu.w
                      and milieu.y < y < milieu.bas)
            self.assertFalse(dedans, "un point du tracé tombe dans la boîte du milieu")

    def test_etiquettes_ne_se_chevauchent_pas(self):
        carte = _carte(["h1", "h2", "f"], [("h1", "f", "60 %"), ("h2", "f", "40 %")])
        plan = dsp.disposer(carte)
        portees = [a for a in plan.aretes if a.etiquette]
        self.assertEqual(len(portees), 2)
        memes = [a for a in portees if abs(a.etiquette_xy[1] - portees[0].etiquette_xy[1]) < 0.1]
        if len(memes) == 2:
            ecart = abs(memes[0].etiquette_xy[0] - memes[1].etiquette_xy[0])
            self.assertGreater(ecart, 20, "deux étiquettes collées sont illisibles")

    def test_libelle_trop_long_est_replie_puis_coupe(self):
        carte = modele.Carte()
        carte.boites.append(modele.Boite(
            cle="a", titre="Investissements Transcontinentaux et Manufacturiers "
                           "de la Rive-Sud incorporée"))
        plan = dsp.disposer(carte)
        boite = plan.boite("a")
        self.assertLessEqual(len(boite.lignes_titre), 2)
        self.assertTrue(boite.lignes_titre[-1].endswith("…"))

    def test_svg_est_un_xml_bien_forme(self):
        plan = dsp.disposer(_carte(["a", "b"], [("a", "b", "100 %")],
                                   titre="Titre & esperluette"))
        racine = ET.fromstring(svg.rendre(plan))
        self.assertTrue(racine.tag.endswith("svg"))
        self.assertIn("viewBox", racine.attrib)

    def test_pdf_est_un_pdf(self):
        plan = dsp.disposer(_carte(["a", "b"], [("a", "b")], titre="Essai"))
        contenu = pdf.rendre(plan)
        self.assertTrue(contenu.startswith(b"%PDF-"))
        self.assertGreater(len(contenu), 1000)

    def test_le_meme_plan_sert_aux_deux_rendus(self):
        """Une géométrie, deux sorties : les coordonnées ne se recalculent pas."""
        carte = _carte(["a", "b"], [("a", "b")])
        plan = dsp.disposer(carte)
        avant = [(b.cle, b.x, b.y, b.w, b.h) for b in plan.boites]
        svg.rendre(plan)
        pdf.rendre(plan)
        apres = [(b.cle, b.x, b.y, b.w, b.h) for b in plan.boites]
        self.assertEqual(avant, apres, "un rendu a bougé la géométrie de l'autre")
