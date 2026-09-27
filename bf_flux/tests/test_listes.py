# -*- coding: utf-8 -*-
from odoo.exceptions import ValidationError
from odoo.tests.common import tagged

from .common import FluxCase

REGLES_DEFENSE = {
    "termes": "défense\nforces armées\narmed forces\nre:\\bcombat\\b(?! sports)\ndrone",
    "emetteurs_seuls": "Lockheed",
    "emetteurs_notes": "Bombardier",
    "exclusions": "conference call\nearnings call",
}


@tagged("post_install", "-at_install")
class TestListes(FluxCase):

    def _liste(self, **vals):
        base = {"name": "Défense", "source_ids": [(6, 0, (self.src_defense | self.src_aero).ids)]}
        base.update(vals)
        return self.env["bf.flux.liste"].with_user(self.u_gestion).create(base)

    def _relever(self):
        with self.reseau(self.flux_exemple()):
            for src in (self.src_defense, self.src_aero):
                src._flux_relever()._flux_trier_et_diffuser()

    def _motifs(self, liste):
        return {r.element_id.cle: r.motifs for r in liste.retenue_ids}

    def test_regles_dans_l_ordre(self):
        liste = self._liste(**REGLES_DEFENSE)
        self._relever()
        motifs = self._motifs(liste)
        # Émetteur spécialisé : retenu sur son seul nom.
        self.assertEqual(motifs["1001"], "Lockheed")
        # Terme du secteur, plus l'émetteur à deux lignes noté.
        self.assertIn("Bombardier", motifs["1002"])
        self.assertIn("armed forces", motifs["1002"])
        # Bombardier civil : son nom seul ne retient rien.
        self.assertNotIn("1003", motifs)
        # Convocation d'appel de résultats : exclue malgré « drone ».
        self.assertNotIn("1004", motifs)
        # « Combat Sports » : le motif resserré ne le prend plus.
        self.assertNotIn("1005", motifs)

    def test_sans_terme_prend_toute_la_source(self):
        liste = self._liste(exclusions="conference call")
        self._relever()
        self.assertEqual(set(self._motifs(liste)), {"1001", "1002", "1003", "1005"})

    def test_insensible_aux_accents(self):
        liste = self._liste(termes="defense")
        self._relever()
        self.assertIn("1002", self._motifs(liste))

    def test_une_seule_retenue_par_element(self):
        liste = self._liste(**REGLES_DEFENSE)
        self._relever()
        self._relever()
        self.assertEqual(len(liste.retenue_ids.filtered(lambda r: r.element_id.cle == "1001")), 1)

    def test_expression_accentuee_telle_quelle(self):
        """Des motifs hérités d'un script existant, écrits avec leurs accents."""
        liste = self._liste(termes="re:forces armées")
        self._relever()
        self.assertIn("1002", self._motifs(liste))
        liste_plate = self._liste(name="Plate", termes="re:forces armees")
        self.env["bf.flux.element"].search([])._flux_trier_et_diffuser()
        self.assertNotIn("1002", self._motifs(liste_plate))

    def test_texte_complet_seulement_pour_les_retenus(self):
        self.src_defense.texte_complet = True
        self._liste(**REGLES_DEFENSE)
        reponses = dict(self.flux_exemple())
        with self.reseau(reponses) as appels:
            for src in (self.src_defense, self.src_aero):
                src._flux_relever()._flux_trier_et_diffuser()
            self.env["bf.flux.element"]._cron_texte_complet()
        pages = [u for u in appels if "news-release" in u]
        self.assertTrue(any("/1001/" in u for u in pages))
        self.assertFalse(any("/1003/" in u for u in pages), "Bombardier civil, non retenu")

    def test_sources_prises_en_entier(self):
        """Une veille type : Aérospatiale en entier, Défense filtrée, une seule liste."""
        liste = self._liste(sources_entieres_ids=[(6, 0, self.src_aero.ids)], **REGLES_DEFENSE)
        with self.reseau(self.flux_exemple()):
            for src in (self.src_aero, self.src_defense):
                src._flux_relever()._flux_trier_et_diffuser()
        motifs = self._motifs(liste)
        # 1001 arrive d'abord par Aérospatiale : pris en entier.
        self.assertEqual(motifs["1001"], "toute la source")
        # 1003 n'est que dans Défense : filtré, donc écarté.
        self.assertNotIn("1003", motifs)

    def test_source_entiere_doit_etre_une_source(self):
        with self.assertRaises(ValidationError):
            self._liste(source_ids=[(6, 0, self.src_defense.ids)],
                        sources_entieres_ids=[(6, 0, self.src_aero.ids)])

    def test_appliquer_reserve_a_la_gestion(self):
        from odoo.exceptions import AccessError
        liste = self._liste(**REGLES_DEFENSE)
        with self.assertRaises(AccessError):
            liste.with_user(self.u_atelier).action_appliquer_existants()

    def test_regex_invalide_refusee(self):
        with self.assertRaises(ValidationError):
            self._liste(termes="re:(ouvert")

    def test_liste_ne_voit_que_ses_sources(self):
        liste = self._liste(source_ids=[(6, 0, self.src_aero.ids)])
        self._relever()
        self.assertEqual(set(self._motifs(liste)), {"1001", "1002"})

    def test_rattrapage_sans_diffusion(self):
        with self.reseau(self.flux_exemple()):
            self.src_defense._flux_relever()
        self.env["bf.flux.element"].search([]).write(
            {"date_publication": "2099-01-01 00:00:00"})
        liste = self._liste(**REGLES_DEFENSE)
        liste.with_user(self.u_gestion).action_appliquer_existants()
        self.assertTrue(liste.retenue_ids)
        self.assertTrue(all(liste.retenue_ids.mapped("rattrapage")))
        self.assertFalse(liste.channel_id.message_ids.filtered(
            lambda m: m.message_type == "comment"))
