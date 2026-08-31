# -*- coding: utf-8 -*-
"""La proposition doit préférer le pilier en retard, écarter ce qui est
bloqué, et dire pourquoi."""

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestProposition(TransactionCase):

    def setUp(self):
        super().setUp()
        self.decouverte = self.env["blog.tag.category"].create({
            "name": "Découverte", "is_pillar": True, "target_share": 60.0,
        })
        self.produit = self.env["blog.tag.category"].create({
            "name": "Produit", "is_pillar": True, "target_share": 40.0,
        })
        self.calendar = self.env["bf.editorial.calendar"].create({
            "name": "Flux d'essai",
            "cadence_days": 4,
            "word_floor": 100,
            "require_all_langs": "no",
            "pillar_ids": [(6, 0, (self.decouverte | self.produit).ids)],
        })
        self.draft = self.env.ref("bf_editorial.stage_draft")
        self.published = self.env.ref("bf_editorial.stage_published")

    def _publier(self, pillar, n):
        for i in range(n):
            self.env["bf.editorial.entry"].create({
                "name": "%s %s" % (pillar.name, i),
                "calendar_id": self.calendar.id,
                "pillar_id": pillar.id,
                "stage_id": self.published.id,
                "published_date": "2026-08-%02d 12:00:00" % (i + 1),
            })

    def test_pilier_en_retard_est_identifie(self):
        self._publier(self.produit, 8)
        self._publier(self.decouverte, 2)
        proposal = self.env["bf.editorial.proposal"].create({
            "calendar_id": self.calendar.id,
        })
        proposal.action_compute()
        self.assertEqual(proposal.owed_pillar_id, self.decouverte)

    def test_candidat_du_pilier_en_retard_passe_devant(self):
        self._publier(self.produit, 8)
        self._publier(self.decouverte, 2)
        faible = self.env["bf.editorial.entry"].create({
            "name": "Un billet produit", "calendar_id": self.calendar.id,
            "pillar_id": self.produit.id, "stage_id": self.draft.id,
        })
        fort = self.env["bf.editorial.entry"].create({
            "name": "Un billet découverte", "calendar_id": self.calendar.id,
            "pillar_id": self.decouverte.id, "stage_id": self.draft.id,
        })
        proposal = self.env["bf.editorial.proposal"].create({
            "calendar_id": self.calendar.id,
        })
        proposal.action_compute()
        self.assertEqual(proposal.line_ids[0].entry_id, fort)
        self.assertIn(faible, proposal.line_ids.mapped("entry_id"))

    def test_entree_bloquee_est_ecartee_avec_son_motif(self):
        amont = self.env["bf.editorial.entry"].create({
            "name": "Prérequis", "calendar_id": self.calendar.id,
            "stage_id": self.draft.id,
        })
        aval = self.env["bf.editorial.entry"].create({
            "name": "Suite", "calendar_id": self.calendar.id,
            "stage_id": self.draft.id, "depends_on_ids": [(6, 0, amont.ids)],
        })
        proposal = self.env["bf.editorial.proposal"].create({
            "calendar_id": self.calendar.id,
        })
        proposal.action_compute()
        self.assertNotIn(aval, proposal.line_ids.mapped("entry_id"))
        self.assertIn("Suite", proposal.blocked_note)

    def test_calendrier_vide_le_dit(self):
        proposal = self.env["bf.editorial.proposal"].create({
            "calendar_id": self.calendar.id,
        })
        proposal.action_compute()
        self.assertIn("Aucun candidat", proposal.recommendation)

    def test_chaque_candidat_porte_un_motif(self):
        self.env["bf.editorial.entry"].create({
            "name": "Un candidat", "calendar_id": self.calendar.id,
            "pillar_id": self.decouverte.id, "stage_id": self.draft.id,
        })
        proposal = self.env["bf.editorial.proposal"].create({
            "calendar_id": self.calendar.id,
        })
        proposal.action_compute()
        self.assertTrue(all(line.rationale for line in proposal.line_ids))

    def test_coquille_vide_ne_passe_pas_devant_un_vrai_brouillon(self):
        """Vécu sur le corpus réel : tous les brouillons étant sous le
        plancher, ils arrivaient ex aequo à zéro et une coquille de 5 mots
        se retrouvait recommandée en premier."""
        vide = self.env["bf.editorial.entry"].create({
            "name": "Titre réservé", "calendar_id": self.calendar.id,
            "pillar_id": self.decouverte.id, "stage_id": self.draft.id,
        })
        presque = self.env["bf.editorial.entry"].create({
            "name": "Brouillon étoffé", "calendar_id": self.calendar.id,
            "pillar_id": self.decouverte.id, "stage_id": self.draft.id,
        })
        fr = self.env.ref("base.lang_fr_CA", raise_if_not_found=False) \
            or self.env["res.lang"].search([("active", "=", True)], limit=1)
        self.env["bf.editorial.version"].create({
            "entry_id": vide.id, "lang_id": fr.id,
            "is_source": True, "word_count": 5,
        })
        self.env["bf.editorial.version"].create({
            "entry_id": presque.id, "lang_id": fr.id,
            "is_source": True, "word_count": 90,
        })
        (vide | presque).invalidate_recordset()
        proposal = self.env["bf.editorial.proposal"].create({
            "calendar_id": self.calendar.id,
        })
        proposal.action_compute()
        rangs = {l.entry_id: l.sequence for l in proposal.line_ids}
        self.assertLess(rangs[presque], rangs[vide])


@tagged("post_install", "-at_install")
class TestPropositionDepuisLeMenu(TransactionCase):
    """La proposition doit être joignable sans passer par un calendrier.

    Elle ne vivait que sur le formulaire de `bf.editorial.calendar`, un écran
    de paramétrage. Depuis la liste des entrées, l'écran où l'on se demande
    justement quoi publier, aucun chemin n'y menait.
    """

    def setUp(self):
        super().setUp()
        self.autre = self.env["res.users"].create({
            "name": "Autre responsable", "login": "autre-editorial@essai.invalid",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id])],
        })
        # ⚠️ Le défaut se calcule sur TOUS les calendriers de la base, pas
        # seulement sur ceux du test. Un banc restauré depuis une prod, ou
        # sali par une sonde manuelle, en porte d'autres — et le test mesurait
        # alors l'état du banc au lieu de la règle. On les écarte d'abord.
        self.env["bf.editorial.calendar"].search([]).write({
            "user_id": self.autre.id, "sequence": 900,
        })
        self.sien = self.env["bf.editorial.calendar"].create({
            "name": "Flux de l'utilisateur", "sequence": 90,
            "user_id": self.env.uid, "require_all_langs": "no",
        })
        self.autre_flux = self.env["bf.editorial.calendar"].create({
            "name": "Flux d'un autre", "sequence": 1,
            "user_id": self.autre.id, "require_all_langs": "no",
        })

    def test_le_menu_rend_une_proposition_deja_calculee(self):
        action = self.env["bf.editorial.proposal"].action_open_next()
        self.assertEqual(action["res_model"], "bf.editorial.proposal")
        self.assertEqual(action["target"], "new")
        proposal = self.env["bf.editorial.proposal"].browse(action["res_id"])
        self.assertTrue(proposal.exists())
        self.assertTrue(proposal.cadence_note,
                        "la proposition doit arriver calculée, pas vide")

    def test_le_calendrier_par_defaut_est_le_sien(self):
        """Même avec une séquence plus haute : c'est le sien qu'on vient voir."""
        self.assertEqual(
            self.env["bf.editorial.proposal"]._default_calendar(), self.sien,
        )

    def test_a_defaut_le_premier_par_sequence(self):
        self.sien.user_id = self.autre
        self.assertEqual(
            self.env["bf.editorial.proposal"]._default_calendar(),
            self.autre_flux,
        )

    def test_recalculer_garde_la_fenetre_ouverte(self):
        proposal = self.env["bf.editorial.proposal"].create({
            "calendar_id": self.sien.id,
        })
        action = proposal.action_recompute()
        self.assertEqual(action["res_id"], proposal.id,
                         "recalculer doit rouvrir la même proposition")
        self.assertTrue(proposal.cadence_note)
