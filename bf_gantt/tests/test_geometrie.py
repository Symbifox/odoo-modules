# -*- coding: utf-8 -*-
"""La géométrie, testée hors de toute base.

Ces contrôles n'ouvrent aucun curseur : `generateur.geometrie` est du Python
ordinaire, et c'est délibéré. Une erreur de placement se voit ici en une
seconde plutôt que dans un PDF qu'il faut ouvrir.
"""
from datetime import date, timedelta

from odoo.tests.common import BaseCase, tagged

from ..generateur import geometrie as geo


def echeancier(taches=None, couloirs=None, deps=None,
               debut="2026-09-01", fin="2026-10-31", aujourdhui="2026-09-20"):
    return {
        "source": {"kind": "project", "model": "project.project", "id": 1},
        "title": "Essai",
        "subtitle": "",
        "company": {"id": 1, "name": "Blue Fox Inc.", "color": "#29ABE1"},
        "grouping": "stage",
        "lanes": couloirs if couloirs is not None else [
            {"key": "a", "name": "Couloir A", "seq": 1, "total": 1, "done": 0, "pct": 0},
        ],
        "tasks": taches if taches is not None else [barre("task-1")],
        "deps": deps or [],
        "range": {"min": debut, "max": fin, "today": aujourdhui},
        "truncated": False,
        "limit": 400,
    }


def barre(ref, lane="a", debut="2026-09-07", fin="2026-09-18", progress=40,
          status="in_progress", jalon=False, origine="planifie", nom=None):
    return {
        "ref": ref, "id": int(ref.split("-")[1]), "name": nom or ("Tâche " + ref),
        "lane": lane, "lane_name": "Couloir A", "lane_seq": 1,
        "start": debut, "end": fin, "start_origin": origine, "deadline": fin,
        "progress": progress, "status": status, "assignee": "Jane D.",
        "allocated_hours": 8.0, "effective_hours": 3.0,
        "is_milestone": jalon, "closed": status in ("done", "canceled"),
    }


def echeancier_marque(logo):
    """Le même échéancier, avec un logo de société."""
    base = echeancier()
    return dict(base, company=dict(base["company"], logo=logo))


@tagged("post_install", "-at_install", "bf_gantt")
class TestGeometrie(BaseCase):

    def test_une_barre_est_placee_a_sa_date(self):
        g = geo.construire(echeancier(), echelle="day")
        ligne = g["lignes"][0]
        jours = (date(2026, 9, 7) - date(2026, 9, 1)).days
        attendu = geo.MARGE_PAGE + g["largeur_libelles"] + jours * g["ppj"]
        self.assertAlmostEqual(ligne["bar_x"], attendu, places=6)

    def test_la_barre_couvre_le_dernier_jour(self):
        """Une tâche du 7 au 7 doit avoir une largeur visible, pas zéro."""
        g = geo.construire(
            echeancier(taches=[barre("task-1", debut="2026-09-07",
                                     fin="2026-09-07")]), echelle="day")
        self.assertGreater(g["lignes"][0]["bar_w"], g["ppj"] * 0.5)

    def test_le_remplissage_suit_l_avancement(self):
        g = geo.construire(echeancier(
            taches=[barre("task-1", progress=50)]), echelle="week")
        ligne = g["lignes"][0]
        self.assertAlmostEqual(ligne["fill_w"], ligne["bar_w"] * 0.5, places=6)

    def test_un_avancement_hors_bornes_est_ramene(self):
        g = geo.construire(echeancier(
            taches=[barre("task-1", progress=500)]), echelle="week")
        ligne = g["lignes"][0]
        self.assertAlmostEqual(ligne["fill_w"], ligne["bar_w"], places=6)

    def test_un_jalon_n_a_pas_de_barre_mais_un_diamant(self):
        g = geo.construire(echeancier(taches=[
            barre("milestone-1", jalon=True, debut="2026-09-10",
                  fin="2026-09-10")]))
        ligne = g["lignes"][0]
        self.assertEqual(ligne["bar_w"], 0.0)
        self.assertIn("diamant", ligne)
        self.assertGreater(ligne["diamant"]["r"], 0)

    def test_un_debut_non_planifie_est_signale(self):
        g = geo.construire(echeancier(taches=[
            barre("task-1", origine="assignation")]))
        self.assertTrue(g["lignes"][0]["approx"])
        g = geo.construire(echeancier(taches=[barre("task-1")]))
        self.assertFalse(g["lignes"][0]["approx"])

    def test_les_couloirs_vides_ne_prennent_pas_de_place(self):
        g = geo.construire(echeancier(couloirs=[
            {"key": "a", "name": "A", "seq": 1, "total": 1, "done": 0, "pct": 0},
            {"key": "z", "name": "Vide", "seq": 2, "total": 0, "done": 0, "pct": 0},
        ]))
        self.assertEqual([c["key"] for c in g["couloirs"]], ["a"])

    def test_la_ligne_du_jour_disparait_hors_plage(self):
        dedans = geo.construire(echeancier(aujourdhui="2026-09-20"))
        self.assertIsNotNone(dedans["x_aujourdhui"])
        dehors = geo.construire(echeancier(aujourdhui="2027-01-01"))
        self.assertIsNone(dehors["x_aujourdhui"])

    def test_une_fleche_relie_deux_barres_connues(self):
        g = geo.construire(echeancier(
            taches=[barre("task-1"), barre("task-2", debut="2026-09-21",
                                           fin="2026-09-30")],
            deps=[{"from": "task-1", "to": "task-2"}]))
        self.assertEqual(len(g["fleches"]), 1)
        self.assertEqual(g["fleches"][0]["from"], "task-1")

    def test_une_fleche_vers_l_inconnu_est_ignoree(self):
        """Une dépendance hors du périmètre affiché ne doit pas planter."""
        g = geo.construire(echeancier(
            taches=[barre("task-1")],
            deps=[{"from": "task-99", "to": "task-1"}]))
        self.assertEqual(g["fleches"], [])

    def test_une_fleche_qui_remonte_contourne_par_le_bas(self):
        """L'aval commence avant la fin de l'amont : six points, pas quatre."""
        g = geo.construire(echeancier(
            taches=[barre("task-1", debut="2026-09-07", fin="2026-09-28"),
                    barre("task-2", debut="2026-09-09", fin="2026-09-14")],
            deps=[{"from": "task-1", "to": "task-2"}]), echelle="day")
        self.assertEqual(len(g["fleches"][0]["points"]), 6)

    def test_les_trois_echelles_donnent_trois_largeurs(self):
        largeurs = [geo.construire(echeancier(), echelle=e)["largeur"]
                    for e in ("day", "week", "month")]
        self.assertTrue(largeurs[0] > largeurs[1] > largeurs[2])

    def test_une_echelle_inconnue_retombe_sur_la_semaine(self):
        g = geo.construire(echeancier(), echelle="siecle")
        self.assertEqual(g["echelle"], "week")

    def test_les_fins_de_semaine_ne_sont_bandees_qu_au_jour(self):
        self.assertTrue(geo.construire(echeancier(), echelle="day")["bandes"])
        self.assertFalse(geo.construire(echeancier(), echelle="week")["bandes"])

    def test_le_plafond_de_lignes_coupe_et_le_dit(self):
        taches = [barre("task-%s" % i) for i in range(1, 11)]
        g = geo.construire(echeancier(taches=taches), max_lignes=4)
        self.assertEqual(len(g["lignes"]), 4)
        self.assertTrue(g["tronque"])

    def test_une_fin_avant_le_debut_ne_produit_pas_de_largeur_negative(self):
        g = geo.construire(echeancier(taches=[
            barre("task-1", debut="2026-09-20", fin="2026-09-10")]))
        self.assertGreaterEqual(g["lignes"][0]["bar_w"], geo.BARRE_MINIMALE)

    def test_couper_respecte_la_largeur(self):
        mesurer = lambda t: len(t) * 5.0
        self.assertEqual(geo.couper("court", 100, mesurer), "court")
        coupe = geo.couper("un titre nettement trop long pour la colonne",
                           50, mesurer)
        self.assertTrue(coupe.endswith("…"))
        self.assertLessEqual(mesurer(coupe), 50)

    def test_couper_sur_une_largeur_nulle_rend_l_ellipse(self):
        self.assertEqual(geo.couper("abc", 0, lambda t: len(t) * 5.0), "…")

    def test_la_hauteur_grandit_avec_les_lignes(self):
        petite = geo.construire(echeancier())["hauteur"]
        grande = geo.construire(echeancier(
            taches=[barre("task-%s" % i) for i in range(1, 21)]))["hauteur"]
        self.assertGreater(grande, petite + 19 * geo.HAUTEUR_LIGNE - 1)

    def test_le_bandeau_grandit_pour_loger_un_logo_matriciel(self):
        """Sans cela, le repère de lecture bute dans le libellé « aujourd'hui »."""
        from .test_echange import LOGO_B64, LOGO_SVG
        nu = geo.construire(echeancier())
        avec = geo.construire(echeancier_marque(LOGO_B64))
        self.assertGreater(avec["hauteur_entete"], nu["hauteur_entete"])
        self.assertGreater(avec["y_axe"], nu["y_axe"])

    def test_un_logo_vectoriel_ne_grandit_pas_le_bandeau(self):
        """Il n'est pas dessiné dans les rendus matriciels : rien à loger."""
        from .test_echange import LOGO_SVG
        nu = geo.construire(echeancier())
        avec = geo.construire(echeancier_marque(LOGO_SVG))
        self.assertEqual(avec["hauteur_entete"], nu["hauteur_entete"])

    def test_une_plage_absurde_est_ramenee_et_le_dit(self):
        """🔴 Une seule échéance mal tapée ouvrait mille ans de graduations, une
        par jour, sur une route publique et sans limitation de débit."""
        g = geo.construire(echeancier(debut="2026-01-01", fin="3026-01-01",
                                      aujourdhui="2026-06-01"), echelle="day")
        self.assertTrue(g["plage_reduite"])
        self.assertLessEqual(
            (date.fromisoformat(g["fin"]) - date.fromisoformat(g["debut"])).days,
            geo.PLAGE_MAX_JOURS)
        self.assertLessEqual(len(g["graduations"]["bas"]), geo.GRADUATIONS_MAX)
        self.assertLess(g["largeur"], 200_000)

    def test_une_barre_hors_fenetre_est_ramenee_dans_le_cadre(self):
        """🔴 Le défaut que le plafond avait créé : la barre gardait ses
        coordonnées d'origine et se traçait hors du cadre, invisible, pendant
        que son nom gardait sa ligne. Un document à qui il manque une ligne,
        sans rien dire, est pire que le mille-ans qu'on venait de corriger."""
        g = geo.construire(echeancier(
            taches=[barre("task-1", debut="2026-01-05", fin="2026-02-05"),
                    barre("task-2", debut="3025-01-01", fin="3026-01-01",
                          nom="Après la fenêtre")],
            debut="2026-01-01", fin="3026-01-01", aujourdhui="2026-06-01"),
            echelle="week")
        self.assertTrue(g["plage_reduite"])
        for ligne in g["lignes"]:
            self.assertGreaterEqual(ligne["bar_x"], 0, ligne["name"])
            self.assertLessEqual(ligne["bar_x"] + ligne["bar_w"], g["largeur"],
                                 ligne["name"])
        hors = next(l for l in g["lignes"] if l["name"] == "Après la fenêtre")
        self.assertTrue(hors["deborde_apres"],
                        "une barre coupée doit le dire pour porter son chevron")

    def test_une_barre_dans_la_fenetre_ne_se_dit_pas_coupee(self):
        g = geo.construire(echeancier())
        self.assertFalse(g["lignes"][0]["deborde_avant"])
        self.assertFalse(g["lignes"][0]["deborde_apres"])

    def test_une_plage_normale_n_est_pas_signalee_comme_reduite(self):
        self.assertFalse(geo.construire(echeancier())["plage_reduite"])

    def test_une_annee_extreme_ne_leve_pas_OverflowError(self):
        """À l'an 9999, `date.max` est atteint : la boucle doit s'arrêter, pas
        déborder. Avant la garde, la route publique rendait un 500."""
        g = geo.construire(echeancier(debut="9990-01-01", fin="9999-12-31",
                                      aujourdhui="9995-01-01"), echelle="month")
        self.assertGreater(g["largeur"], 0)

    def test_une_plage_d_un_seul_jour_ne_divise_pas_par_zero(self):
        g = geo.construire(echeancier(debut="2026-09-10", fin="2026-09-10",
                                      aujourdhui="2026-09-10"))
        self.assertGreater(g["largeur"], 0)
