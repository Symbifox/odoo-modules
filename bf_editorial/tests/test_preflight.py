# -*- coding: utf-8 -*-
"""La garde de pré-vol est le seul point qui empêche une publication à
moitié prête. Elle doit refuser pour la bonne raison, et laisser passer
quand tout est en ordre."""

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestPreflight(TransactionCase):

    def setUp(self):
        super().setUp()
        self.calendar = self.env["bf.editorial.calendar"].create({
            "name": "Banc d'essai",
            "cadence_days": 4,
            "word_floor": 100,
            "require_all_langs": "no",
        })
        self.stage_draft = self.env.ref("bf_editorial.stage_draft")
        self.entry = self.env["bf.editorial.entry"].create({
            "name": "Entrée d'essai",
            "calendar_id": self.calendar.id,
            "stage_id": self.stage_draft.id,
        })

    def test_qa_non_passee_bloque(self):
        problems = self.entry._preflight_problems()
        self.assertTrue(any("QA" in p for p in problems))

    def test_publication_refusee_leve_une_erreur_explicite(self):
        with self.assertRaises(UserError) as caught:
            self.entry.action_publish()
        self.assertIn("QA", str(caught.exception))

    def test_reste_bloquant_retient(self):
        # L'entrée naît avec les restes de ses gabarits : c'est voulu.
        # Ce test porte sur autre chose, on part donc d'une liste vide.
        self.entry.checklist_ids.unlink()
        self.entry.qa_state = "clean"
        self.env["bf.editorial.checklist"].create({
            "entry_id": self.entry.id,
            "name": "Visuels à produire",
            "is_blocking": True,
        })
        self.entry.invalidate_recordset()
        self.assertTrue(
            any("reste" in p for p in self.entry._preflight_problems())
        )

    def test_reste_non_bloquant_laisse_passer(self):
        # L'entrée naît avec les restes de ses gabarits : c'est voulu.
        # Ce test porte sur autre chose, on part donc d'une liste vide.
        self.entry.checklist_ids.unlink()
        self.entry.qa_state = "clean"
        self.env["bf.editorial.checklist"].create({
            "entry_id": self.entry.id,
            "name": "Blurbs",
            "is_blocking": False,
        })
        self.entry.invalidate_recordset()
        self.assertFalse(
            any("reste" in p for p in self.entry._preflight_problems())
        )

    def test_dependance_non_publiee_bloque(self):
        amont = self.env["bf.editorial.entry"].create({
            "name": "Doit sortir avant",
            "calendar_id": self.calendar.id,
            "stage_id": self.stage_draft.id,
        })
        self.entry.depends_on_ids = amont
        self.entry.invalidate_recordset()
        self.assertTrue(self.entry.is_blocked)
        self.assertIn("Doit sortir avant", self.entry.blocking_summary)

    def test_entree_prete_passe(self):
        # L'entrée naît avec les restes de ses gabarits : c'est voulu.
        # Ce test porte sur autre chose, on part donc d'une liste vide.
        self.entry.checklist_ids.unlink()
        self.entry.qa_state = "clean"
        self.entry.invalidate_recordset()
        self.assertEqual(self.entry._preflight_problems(), [])
        self.assertTrue(self.entry.preflight_ok)

    def test_publication_pose_la_date_et_l_etape(self):
        # L'entrée naît avec les restes de ses gabarits : c'est voulu.
        # Ce test porte sur autre chose, on part donc d'une liste vide.
        self.entry.checklist_ids.unlink()
        self.entry.qa_state = "clean"
        self.entry.invalidate_recordset()
        self.entry.action_publish()
        self.assertTrue(self.entry.published_date)
        self.assertTrue(self.entry.stage_id.is_closing)

    def test_cron_ne_publie_pas_une_entree_non_prete(self):
        """Le point le plus important du module : une date approuvée ne passe
        pas outre la garde. Sinon un article part sans sa traduction."""
        # L'entrée naît avec les restes de ses gabarits : c'est voulu.
        # Ce test porte sur autre chose, on part donc d'une liste vide.
        self.entry.checklist_ids.unlink()
        self.entry.scheduled_publish_date = "2000-01-01 00:00:00"
        self.env["bf.editorial.entry"]._cron_publish_scheduled()
        self.assertFalse(self.entry.published_date)
        self.assertTrue(self.entry.activity_ids)

    def test_cron_publie_une_entree_prete(self):
        # L'entrée naît avec les restes de ses gabarits : c'est voulu.
        # Ce test porte sur autre chose, on part donc d'une liste vide.
        self.entry.checklist_ids.unlink()
        self.entry.qa_state = "clean"
        self.entry.scheduled_publish_date = "2000-01-01 00:00:00"
        self.entry.invalidate_recordset()
        self.env["bf.editorial.entry"]._cron_publish_scheduled()
        self.assertTrue(self.entry.published_date)

    def test_gabarits_poses_a_la_creation(self):
        """Les gabarits étaient livrés morts : rien ne les appliquait, et la
        méthode privée n'était pas joignable de l'extérieur."""
        self.env["bf.editorial.checklist.template"].create({
            "name": "Un reste d'essai", "is_blocking": True,
        })
        neuve = self.env["bf.editorial.entry"].create({
            "name": "Entrée neuve", "calendar_id": self.calendar.id,
        })
        self.assertTrue(neuve.checklist_ids,
                        "une entrée neuve doit naître avec sa liste de contrôle")
        self.assertIn("Un reste d'essai", neuve.checklist_ids.mapped("name"))

    def test_date_calendrier_retombe_sur_la_publication(self):
        """Vécu : la vue calendrier était calée sur la seule date prévue,
        vide pour 164 entrées sur 165. Elle n'affichait qu'un item."""
        self.entry.checklist_ids.unlink()
        self.entry.qa_state = "clean"
        self.entry.invalidate_recordset()
        self.assertFalse(self.entry.timeline_date,
                         "sans date, rien à placer au calendrier")
        self.entry.action_publish()
        self.entry.invalidate_recordset()
        self.assertTrue(self.entry.timeline_date,
                        "une entrée publiée doit apparaître au calendrier")
        self.assertEqual(self.entry.timeline_date,
                         self.entry.published_date.date())

    def test_date_prevue_prime_sur_la_publication(self):
        self.entry.planned_date = "2026-09-15"
        self.entry.invalidate_recordset()
        self.assertEqual(str(self.entry.timeline_date), "2026-09-15")


@tagged("post_install", "-at_install")
class TestPreflightLangues(TransactionCase):
    """La politique multilingue, que la suite ne couvrait nulle part.

    Tous les cas de garde étaient écrits avec ``require_all_langs = "no"``.
    Le seul chemin où la politique s'applique n'était donc jamais parcouru,
    et il refusait tout : la garde exigeait l'état « publiée » sur chaque
    créneau, un état que rien n'atteint avant la publication.
    """

    def setUp(self):
        super().setUp()
        Lang = self.env["res.lang"]
        Lang._activate_lang("fr_FR")
        Lang._activate_lang("en_US")
        self.fr = Lang.search([("code", "=", "fr_FR")], limit=1)
        self.en = Lang.search([("code", "=", "en_US")], limit=1)
        self.calendar = self.env["bf.editorial.calendar"].create({
            "name": "Banc bilingue",
            "cadence_days": 4,
            "word_floor": 0,
            "require_all_langs": "yes",
            "lang_ids": [(6, 0, (self.fr | self.en).ids)],
        })
        self.entry = self.env["bf.editorial.entry"].create({
            "name": "Entrée bilingue",
            "calendar_id": self.calendar.id,
            "stage_id": self.env.ref("bf_editorial.stage_draft").id,
        })
        self.entry.checklist_ids.unlink()
        self.entry.qa_state = "clean"
        self.versions = self.env["bf.editorial.version"].create([
            {"entry_id": self.entry.id, "lang_id": self.fr.id,
             "is_source": True, "state": "todo", "word_count": 2000},
            {"entry_id": self.entry.id, "lang_id": self.en.id,
             "state": "todo", "word_count": 1800},
        ])
        self.entry.invalidate_recordset()

    def test_creneau_a_traduire_retient(self):
        self.assertFalse(self.entry.langs_ready)
        self.assertTrue(
            any("langues" in p for p in self.entry._preflight_problems())
        )

    def test_creneau_manquant_retient(self):
        self.versions[1].unlink()
        self.entry.invalidate_recordset()
        self.assertFalse(self.entry.langs_ready)
        self.assertIn("aucun créneau", self.entry.language_summary)

    def test_langues_relues_laissent_passer(self):
        """La régression : deux créneaux relus doivent ouvrir la garde.

        Avant le correctif, ce cas refusait encore, parce que « relue » n'est
        pas « publiée » et que rien ne franchissait jamais ce dernier pas.
        """
        self.versions.write({"state": "reviewed"})
        self.entry.invalidate_recordset()
        self.assertTrue(self.entry.langs_ready)
        self.assertFalse(self.entry.langs_complete,
                         "relue n'est pas livrée : les deux états diffèrent")
        self.assertEqual(self.entry._preflight_problems(), [])

    def test_publication_sort_les_creneaux(self):
        self.versions.write({"state": "reviewed"})
        self.entry.invalidate_recordset()
        self.entry.action_publish()
        self.entry.invalidate_recordset()
        self.assertTrue(self.entry.published_date)
        self.assertEqual(set(self.versions.mapped("state")), {"published"},
                         "les créneaux doivent sortir avec l'article")
        self.assertTrue(self.entry.langs_complete)

    def test_cron_publie_une_entree_bilingue_prete(self):
        self.versions.write({"state": "reviewed"})
        self.entry.scheduled_publish_date = "2000-01-01 00:00:00"
        self.entry.invalidate_recordset()
        self.env["bf.editorial.entry"]._cron_publish_scheduled()
        self.assertTrue(self.entry.published_date)
        self.assertEqual(set(self.versions.mapped("state")), {"published"})

    def test_cron_refuse_une_traduction_en_retard(self):
        self.versions[0].state = "reviewed"
        self.entry.scheduled_publish_date = "2000-01-01 00:00:00"
        self.entry.invalidate_recordset()
        self.env["bf.editorial.entry"]._cron_publish_scheduled()
        self.assertFalse(self.entry.published_date)
        self.assertTrue(self.entry.activity_ids)
