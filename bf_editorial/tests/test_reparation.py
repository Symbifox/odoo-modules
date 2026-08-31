# -*- coding: utf-8 -*-
"""Le réparateur mécanique ne doit corriger QUE ce qu'il annonce.

Un correcteur automatique qui déborde d'un cheveu est pire qu'aucun
correcteur : on ne peut plus lui faire confiance sur le reste, donc on relit
tout, donc il ne sert plus à rien. La moitié de ces tests dit ce qu'il fait,
l'autre moitié dit ce qu'il ne touche pas.
"""

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from odoo.addons.bf_editorial.models import reparation


@tagged("post_install", "-at_install")
class TestReparation(TransactionCase):

    # --- ce qu'il répare --------------------------------------------------
    def test_titre_vide_devient_un_espacement(self):
        corrige, rapport = reparation.corriger(
            "<h2><br></h2>\n<h3>Un vrai titre</h3>"
        )
        self.assertEqual(rapport["titres_vides"], 1)
        self.assertIn("<p><br></p>", corrige)
        self.assertNotIn("<h2>", corrige)
        self.assertIn("<h3>Un vrai titre</h3>", corrige)

    def test_titre_vide_a_tous_les_niveaux(self):
        corrige, rapport = reparation.corriger(
            "<h1>  </h1><h4>&nbsp;</h4><h6><br/></h6>"
        )
        self.assertEqual(rapport["titres_vides"], 3)
        self.assertEqual(corrige, "<p><br></p>" * 3)

    def test_entete_de_colonne_recoit_col(self):
        corrige, rapport = reparation.corriger(
            "<table><thead><tr><th>A</th><th>B</th></tr></thead></table>"
        )
        self.assertEqual(rapport["portees"], 2)
        self.assertEqual(corrige.count('scope="col"'), 2)

    def test_entete_de_ligne_recoit_row(self):
        corrige, rapport = reparation.corriger(
            "<table><tbody><tr><th>Coût</th><td>0 $</td><td>10 $</td>"
            "</tr></tbody></table>"
        )
        self.assertEqual(rapport["portees"], 1)
        self.assertIn('<th scope="row">', corrige)

    def test_attributs_conserves(self):
        corrige, _rapport = reparation.corriger(
            '<table><tr><th class="x" id="y">A</th></tr></table>'
        )
        self.assertIn('<th scope="col" class="x" id="y">', corrige)

    # --- ce qu'il ne touche pas ------------------------------------------
    def test_thead_nest_pas_un_th(self):
        """« th » est un préfixe littéral de « thead ». Sans limite de mot, le
        réparateur poserait une portée sur la balise de section."""
        corrige, _rapport = reparation.corriger(
            "<table><thead><tr><td>A</td></tr></thead></table>"
        )
        self.assertNotIn("scope", corrige)
        self.assertIn("<thead>", corrige)

    def test_portee_existante_intacte(self):
        html = '<table><tr><th scope="row">A</th><td>B</td></tr></table>'
        corrige, rapport = reparation.corriger(html)
        self.assertEqual(rapport["portees"], 0)
        self.assertEqual(corrige, html)

    def test_ligne_ambigue_pas_touchee(self):
        """Deux en-têtes puis des données : ni des titres de colonnes, ni un
        titre de ligne. Deviner serait un jugement."""
        html = "<table><tr><th>A</th><th>B</th><td>C</td></tr></table>"
        corrige, rapport = reparation.corriger(html)
        self.assertEqual(rapport["portees"], 0)
        self.assertEqual(corrige, html)

    def test_titre_avec_du_texte_intact(self):
        html = "<h2>Un titre qui dit quelque chose</h2>"
        corrige, rapport = reparation.corriger(html)
        self.assertEqual(rapport["titres_vides"], 0)
        self.assertEqual(corrige, html)

    def test_niveaux_depareilles_pas_touches(self):
        """<h2>…</h3> est du HTML cassé, pas un titre vide. Le réparer en
        silence masquerait le vrai problème."""
        html = "<h2></h3>"
        corrige, rapport = reparation.corriger(html)
        self.assertEqual(rapport["titres_vides"], 0)
        self.assertEqual(corrige, html)

    def test_ce_qui_demande_un_jugement_reste_dehors(self):
        html = (
            "<p>Un tiret — cadratin.</p>"
            "<p>La bonne nouvelle, c'est que ça marche.</p>"
            "<!-- IMAGE à produire -->"
            '<img src="/a.png">'
        )
        corrige, rapport = reparation.corriger(html)
        self.assertEqual(corrige, html)
        self.assertEqual(rapport, {"titres_vides": 0, "portees": 0})

    def test_vide_et_faux_ne_plantent_pas(self):
        for entree in ("", False, None):
            corrige, rapport = reparation.corriger(entree)
            self.assertEqual(corrige, entree)
            self.assertEqual(rapport["titres_vides"], 0)


@tagged("post_install", "-at_install")
class TestReparationSurUnBillet(TransactionCase):
    """Le bouton écrit dans un vrai billet, créneau par créneau.

    C'est le seul endroit du module qui touche au contenu. Ce qu'on vérifie
    ici, c'est qu'il écrit dans le bon créneau, qu'il ne déborde pas sur les
    autres, et qu'il laisse derrière lui un état de QA qui dit la vérité.
    """

    def setUp(self):
        super().setUp()
        blogue = self.env["blog.blog"].create({"name": "Banc"})
        self.billet = self.env["blog.post"].create({
            "name": "Billet d'essai",
            "blog_id": blogue.id,
            "content": (
                "<h2><br></h2>\n<h3>Un titre</h3>"
                "<table><thead><tr><th>Colonne</th></tr></thead></table>"
            ),
        })
        self.calendrier = self.env["bf.editorial.calendar"].create({
            "name": "Réparation", "require_all_langs": "no", "word_floor": 0,
        })
        self.entree = self.env["bf.editorial.entry"].create({
            "name": "Entrée à réparer",
            "calendar_id": self.calendrier.id,
            "post_id": self.billet.id,
        })
        self.entree.checklist_ids.unlink()

    def test_le_bouton_repare_et_repasse_la_qa(self):
        self.entree.action_fix_mechanical()
        self.billet.invalidate_recordset(["content"])
        contenu = self.billet.content
        self.assertNotIn("<h2><br></h2>", contenu)
        self.assertIn("<p><br></p>", contenu)
        self.assertIn('scope="col"', contenu)
        self.assertIn("<h3>Un titre</h3>", contenu)
        self.entree.invalidate_recordset()
        self.assertNotEqual(
            self.entree.qa_state, "todo",
            "la QA doit être repassée dans la foulée, pas laissée à repasser",
        )

    def test_un_billet_deja_propre_ne_bouge_pas(self):
        self.billet.content = "<h2>Un titre</h2><p>Du texte.</p>"
        avant = self.billet.content
        self.entree.action_fix_mechanical()
        self.billet.invalidate_recordset(["content"])
        self.assertEqual(self.billet.content, avant)

    def test_sans_billet_le_bouton_refuse(self):
        orpheline = self.env["bf.editorial.entry"].create({
            "name": "Sans billet", "calendar_id": self.calendrier.id,
        })
        with self.assertRaises(UserError):
            orpheline.action_fix_mechanical()

    def test_le_chatter_dit_ce_qui_a_ete_corrige(self):
        self.entree.action_fix_mechanical()
        corps = " ".join(c or "" for c in self.entree.message_ids.mapped("body"))
        self.assertIn("titre(s) vide(s)", corps)
        self.assertIn("portée(s)", corps)
