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
        """⚠️ La première version de cet essai portait un `if` qui ne se
        déclenchait jamais : la mutation « étiquettes jamais décollées » passait
        au vert. On mesure donc le décolleur DIRECTEMENT, sur des positions
        fabriquées pour se chevaucher, et le chemin complet ensuite."""
        aretes = [
            dsp.AretePlan(de="a", vers="c", points=[(0, 0), (0, 50)],
                          etiquette="60 % · catégorie A", etiquette_xy=(100.0, 200.0)),
            dsp.AretePlan(de="b", vers="c", points=[(0, 0), (0, 50)],
                          etiquette="40 % · catégorie B", etiquette_xy=(112.0, 200.0)),
        ]
        dsp._decoller_les_etiquettes(aretes)
        self.assertNotEqual(aretes[0].etiquette_xy[1], aretes[1].etiquette_xy[1],
                            "deux étiquettes qui se mordent doivent se décoller")

    def test_deux_etiquettes_eloignees_ne_bougent_pas(self):
        aretes = [
            dsp.AretePlan(de="a", vers="c", points=[(0, 0)], etiquette="60 %",
                          etiquette_xy=(100.0, 200.0)),
            dsp.AretePlan(de="b", vers="c", points=[(0, 0)], etiquette="40 %",
                          etiquette_xy=(400.0, 200.0)),
        ]
        dsp._decoller_les_etiquettes(aretes)
        self.assertEqual([a.etiquette_xy[1] for a in aretes], [200.0, 200.0],
                         "rien ne justifie de déplacer des étiquettes qui ne se touchent pas")

    def test_le_chemin_complet_decolle_aussi(self):
        carte = _carte(["h1", "h2", "f"],
                       [("h1", "f", "60 % · catégorie A privilégiée"),
                        ("h2", "f", "40 % · catégorie B subalterne")])
        plan = dsp.disposer(carte)
        portees = [a for a in plan.aretes if a.etiquette]
        self.assertEqual(len(portees), 2)
        for i, a in enumerate(portees):
            for b in portees[i + 1:]:
                if abs(a.etiquette_xy[1] - b.etiquette_xy[1]) < 0.1:
                    demi = 60
                    self.assertGreater(abs(a.etiquette_xy[0] - b.etiquette_xy[0]), demi,
                                       "deux étiquettes collées sur le même couloir")

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


@tagged("post_install", "-at_install", "bf_org_chart")
class TestMoteurRobustesse(TransactionCase):
    """Ce que la revue adverse du 2026-09-13 a trouvé, et qui ne doit plus revenir."""

    def test_un_mot_long_qui_suit_un_mot_court_est_coupe(self):
        """🔴 La coupe au caractère ne valait que pour un mot en PREMIÈRE
        position : ailleurs, la ligne débordait de 41 pt de chaque côté et
        mordait sur la boîte voisine."""
        from ..moteur import mesure
        utile = dsp.BOITE_L - 2 * dsp.MARGE_INT
        for texte in ("Investissementstranscontinentauxetmanufacturiers",
                      "A Investissementstranscontinentauxetmanufacturiers",
                      "Le groupe Investissementstranscontinentauxdelarivesud"):
            lignes = mesure.replier(texte, dsp.T_TITRE, utile, 2, gras=True)
            for ligne in lignes:
                self.assertLessEqual(
                    mesure.largeur(ligne, dsp.T_TITRE, gras=True), utile + 0.5,
                    "« %s » déborde de sa boîte" % ligne)

    def test_le_coupeur_de_boucles_ne_casse_pas_la_pile(self):
        """🔴 Les trois parcours étaient récursifs : RecursionError vers 498
        boîtes, pour un plafond de 400. Ici on éprouve la profondeur SEULE, sans
        la toile, sur une chaîne cinq mille fois plus longue que la pile Python
        n'en aurait porté."""
        cles = ["n%04d" % i for i in range(5000)]
        carte = _carte(cles, [(cles[i], cles[i + 1]) for i in range(len(cles) - 1)])
        carte.valider()
        enfants, parents = dsp._graphe(carte)
        self.assertEqual(dsp._couper_les_boucles(carte, enfants, parents), [])
        self.assertEqual(len(dsp._ordre_descendant(["n0000"], enfants)), 5000)

    def test_une_chaine_profonde_se_dessine_quand_la_toile_le_permet(self):
        """La disposition elle-même, à une profondeur que la pile récursive ne
        tenait pas. Les plafonds de toile sont écartés : ils ne sont pas le
        sujet de cet essai."""
        from unittest.mock import patch
        cles = ["n%03d" % i for i in range(600)]
        carte = _carte(cles, [(cles[i], cles[i + 1]) for i in range(len(cles) - 1)])
        with patch.object(dsp, "PLAFOND_COTE", 1e9), \
             patch.object(dsp, "PLAFOND_SURFACE", 1e15):
            plan = dsp.disposer(carte)
        self.assertEqual(len(plan.boites), 600)
        self.assertEqual(len({round(b.y) for b in plan.boites}), 600)

    def test_les_relais_sont_bornes(self):
        """⚠️ L'essai vérifie le MOTIF du refus, pas seulement qu'il y en a un :
        sans cela, la borne de toile refusait à la place de la borne de relais
        et la mutation passait inaperçue."""
        from ..moteur import modele
        cles = ["r%03d" % i for i in range(300)]
        aretes = [(cles[i], cles[i + 1]) for i in range(len(cles) - 1)]
        aretes += [(cles[0], c) for c in cles[2:]]
        with self.assertRaises(modele.CarteTropGrande) as pris:
            dsp.disposer(_carte(cles, aretes))
        self.assertIn("couloirs", str(pris.exception),
                      "c'est la borne des relais qui doit mordre ici")

    def test_une_toile_demesuree_est_refusee(self):
        from ..moteur import modele
        cles = ["e%03d" % i for i in range(390)]
        with self.assertRaises(modele.CarteTropGrande):
            dsp.disposer(_carte(cles, [("e000", c) for c in cles[1:]]))

    def test_un_lien_actif_ne_sort_pas_dans_le_dessin(self):
        """🔴 `quoteattr` empêche de sortir de l'attribut, pas d'y mettre un
        schéma exécutable. Le SVG est injecté en HTML brut."""
        from ..moteur import modele
        carte = modele.Carte(titre="Lien")
        carte.boites.append(modele.Boite(cle="a", titre="A", lien="javascript:alert(1)"))
        carte.boites.append(modele.Boite(cle="b", titre="B", lien="/odoo/contacts/2"))
        plan = dsp.disposer(carte)
        dessin = svg.rendre(plan)
        self.assertNotIn("javascript:", dessin)
        self.assertIn("/odoo/contacts/2", dessin)
        self.assertTrue(pdf.rendre(plan, base_url="https://exemple.test").startswith(b"%PDF-"))

    def test_deux_aretes_identiques_ne_font_pas_deux_parents(self):
        carte = modele.Carte()
        for cle in ("a", "b"):
            carte.boites.append(modele.Boite(cle=cle, titre=cle))
        carte.aretes.append(modele.Arete(de="a", vers="b"))
        carte.aretes.append(modele.Arete(de="a", vers="b"))
        plan = dsp.disposer(carte)
        self.assertEqual(len(plan.aretes), 1)
        self.assertEqual(plan.mode, "arbre")

    def test_quatre_etiquettes_dans_un_couloir_se_decollent_toutes(self):
        aretes = [
            dsp.AretePlan(de=str(i), vers="c", points=[(0, 0)],
                          etiquette="%s %% · catégorie longue" % (20 + i),
                          etiquette_xy=(100.0 + i * 8, 200.0))
            for i in range(4)
        ]
        dsp._decoller_les_etiquettes(aretes)
        hauteurs = [a.etiquette_xy[1] for a in aretes]
        self.assertEqual(len(set(hauteurs)), 4, "deux étiquettes restent superposées")

    def test_la_legende_avance_a_la_mesure_dans_les_deux_rendus(self):
        """Une avance à l'estime d'un côté rouvre la divergence que `mesure`
        existe pour fermer."""
        from ..moteur import mesure
        import re
        carte = _carte(["a"], [], titre="Légende")
        carte.legende = [("bleu", "Détenteur ultime"), ("ambre", "Structure partielle")]
        plan = dsp.disposer(carte)
        dessin = svg.rendre(plan)
        xs = [float(m) for m in re.findall(r'<rect x="([0-9.]+)" y="[0-9.]+" width="10"', dessin)]
        self.assertEqual(len(xs), 2)
        attendu = xs[0] + 22 + mesure.largeur("Détenteur ultime", 8)
        self.assertAlmostEqual(xs[1], attendu, places=1)

    def test_un_avertissement_de_la_source_arrive_au_plan(self):
        carte = _carte(["a"], [])
        carte.avertissements = ["La structure se poursuit plus loin."]
        self.assertEqual(dsp.disposer(carte).avertissements,
                         ["La structure se poursuit plus loin."])
