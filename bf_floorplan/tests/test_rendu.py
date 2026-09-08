# -*- coding: utf-8 -*-
from odoo.tests import tagged

from .commun import CasPlan


@tagged("post_install", "-at_install")
class TestRendu(CasPlan):

    def test_contrat(self):
        d = self.plan.rendu()
        for cle in ("titre", "plan_id", "largeur", "hauteur", "pas", "zones",
                    "elements", "liens", "legende", "modifiable", "fige", "fond",
                    "surligne", "palette", "vide", "places", "occupes"):
            self.assertIn(cle, d)
        self.assertEqual((d["largeur"], d["hauteur"], d["pas"]), (1000.0, 800.0, 25.0))
        self.assertTrue(d["modifiable"])
        self.assertFalse(d["fige"])
        self.assertFalse(d["fond"])
        self.assertFalse(d["surligne"])
        self.assertEqual(d["vide"], "")

    def test_zones_et_elements(self):
        d = self.plan.rendu()
        z = {z["nom"]: z for z in d["zones"]}
        self.assertEqual(z["Aire ouverte"]["couleur"], "#FFF8E1")
        self.assertEqual((z["Aire ouverte"]["occupes"], z["Aire ouverte"]["capacite"]), (1, 4))
        self.assertEqual(z["Aire ouverte"]["elements"], 2)
        e = {e["nom"]: e for e in d["elements"]}
        self.assertEqual(e["P-01"]["occupant"], "Personne du banc")
        self.assertEqual(e["P-01"]["zone"], "Aire ouverte")
        self.assertEqual(e["P-01"]["rot"], 0)
        self.assertFalse(e["P-01"]["cible"])
        self.assertEqual(e["P-01"]["teinte"], "")
        self.assertIn("Personne du banc", e["P-01"]["info"])
        self.assertEqual(e["SW-01"]["liens"], 1)
        self.assertEqual(e["SW-01"]["genre_nom"], "Commutateur réseau")

    def test_liens_entre_les_centres(self):
        d = self.plan.rendu()
        li = d["liens"][0]
        self.assertEqual(li["points"], [[722.5, 112.5], [315.0, 515.0]])
        self.assertEqual(li["etiquette"], "port 3")
        self.assertEqual(li["genre_nom"], "Réseau (cuivre)")

    def test_legende_ne_liste_que_le_present(self):
        d = self.plan.rendu()
        self.assertEqual([z["code"] for z in d["legende"]["zones"]], ["ouvert", "technique"])
        self.assertEqual(sorted(e["code"] for e in d["legende"]["elements"]),
                         ["borne", "commutateur", "poste"])
        self.assertEqual([li["code"] for li in d["legende"]["liens"]], ["reseau"])

    def test_palette_complete(self):
        d = self.plan.rendu()
        self.assertEqual(len(d["palette"]["zones"]), 10)
        self.assertEqual(len(d["palette"]["elements"]), 12)
        self.assertEqual(len(d["palette"]["liens"]), 4)
        poste = next(e for e in d["palette"]["elements"] if e["code"] == "poste")
        self.assertEqual((poste["w"], poste["h"]), (160.0, 80.0))

    def test_fond_par_url_datee(self):
        self.plan.fond = self.fond_b64()
        d = self.plan.rendu()
        self.assertTrue(d["fond"].startswith(f"/web/image/bf.floorplan/{self.plan.id}/fond?unique="))

    def test_surligne_vient_du_contexte(self):
        d = self.plan.with_context(bf_floorplan_surligne=self.poste.id).rendu()
        self.assertEqual(d["surligne"], self.poste.id)

    def test_message_du_plan_vide(self):
        nu = self.Plan.create({"name": "Nu"})
        self.assertIn("fond de plan", nu.rendu()["vide"])
        nu.fond = self.fond_b64()
        self.assertEqual(nu.rendu()["vide"], "")

    def test_lecteur_voit_sans_poignee(self):
        # le cache de l'administrateur ne doit pas répondre à la place du lecteur
        self.env.invalidate_all()
        d = self.plan.with_user(self.lecteur).rendu()
        self.assertFalse(d["modifiable"])
        self.assertEqual(len(d["elements"]), 3)
