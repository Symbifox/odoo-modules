# -*- coding: utf-8 -*-
"""Les deux cases de langue se cochent.

« Toutes les langues relues » et « Toutes les langues livrées » se calculent
depuis les créneaux. Elles se cochent aussi : cocher prend la décision pour
tous les créneaux exigés d'un coup, décocher la retire. Ces essais tiennent
la promesse dans les deux sens, et vérifient que le geste ne déborde jamais
sur un créneau qu'il ne devait pas toucher.
"""

from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestCasesDeLangue(TransactionCase):

    def setUp(self):
        super().setUp()
        Lang = self.env["res.lang"]
        Lang._activate_lang("fr_FR")
        Lang._activate_lang("en_US")
        Lang._activate_lang("es_ES")
        self.fr = Lang.search([("code", "=", "fr_FR")], limit=1)
        self.en = Lang.search([("code", "=", "en_US")], limit=1)
        self.es = Lang.search([("code", "=", "es_ES")], limit=1)
        self.calendar = self.env["bf.editorial.calendar"].create({
            "name": "Banc des cases",
            "cadence_days": 4,
            "word_floor": 0,
            "require_all_langs": "yes",
            "lang_ids": [(6, 0, (self.fr | self.en).ids)],
        })
        self.entry = self.env["bf.editorial.entry"].create({
            "name": "Entrée à cocher",
            "calendar_id": self.calendar.id,
            "stage_id": self.env.ref("bf_editorial.stage_draft").id,
        })
        self.entry.checklist_ids.unlink()
        self.entry.qa_state = "clean"
        self.fr_slot, self.en_slot = self.env["bf.editorial.version"].create([
            {"entry_id": self.entry.id, "lang_id": self.fr.id,
             "is_source": True, "state": "todo", "word_count": 2000},
            {"entry_id": self.entry.id, "lang_id": self.en.id,
             "state": "translated", "word_count": 1800},
        ])
        self.entry.invalidate_recordset()
        self.redaction = self.env["res.users"].create({
            "name": "Rédactrice", "login": "qa_cases_redaction",
            "groups_id": [(6, 0, [
                self.env.ref("base.group_user").id,
                self.env.ref("bf_editorial.group_editorial_user").id,
            ])],
        })

    # --- « Toutes les langues relues » -----------------------------------
    def test_cocher_relues_passe_les_creneaux_exiges_a_relue(self):
        self.assertFalse(self.entry.langs_ready)
        self.entry.write({"langs_ready": True})
        self.entry.invalidate_recordset()
        self.assertEqual(
            set((self.fr_slot | self.en_slot).mapped("state")), {"reviewed"})
        self.assertTrue(self.entry.langs_ready)
        self.assertEqual(self.entry._preflight_problems(), [],
                         "cocher doit ouvrir la garde de pré-vol")

    def test_cocher_relues_cree_le_creneau_manquant(self):
        self.en_slot.unlink()
        self.entry.invalidate_recordset()
        self.assertIn("aucun créneau", self.entry.language_summary)
        self.entry.write({"langs_ready": True})
        self.entry.invalidate_recordset()
        en = self.entry.version_ids.filtered(lambda v: v.lang_id == self.en)
        self.assertEqual(len(en), 1)
        self.assertEqual(en.state, "reviewed")
        self.assertTrue(self.entry.langs_ready)

    def test_cocher_relues_ne_touche_ni_publie_ni_langue_non_exigee(self):
        self.en_slot.state = "published"
        es = self.env["bf.editorial.version"].create({
            "entry_id": self.entry.id, "lang_id": self.es.id, "state": "todo",
        })
        self.entry.invalidate_recordset()
        self.entry.write({"langs_ready": True})
        self.entry.invalidate_recordset()
        self.assertEqual(self.fr_slot.state, "reviewed")
        self.assertEqual(self.en_slot.state, "published",
                         "un créneau publié ne redescend pas à relue")
        self.assertEqual(es.state, "todo",
                         "une langue que le calendrier n'exige pas reste hors du geste")

    def test_decocher_relues_ramene_a_traduite_sans_toucher_le_publie(self):
        self.fr_slot.state = "reviewed"
        self.en_slot.state = "published"
        self.entry.invalidate_recordset()
        self.assertTrue(self.entry.langs_ready)
        self.entry.write({"langs_ready": False})
        self.entry.invalidate_recordset()
        self.assertEqual(self.fr_slot.state, "translated")
        self.assertEqual(self.en_slot.state, "published")
        self.assertFalse(self.entry.langs_ready)

    def test_cocher_relues_est_permis_a_la_redaction(self):
        entry = self.entry.with_user(self.redaction)
        entry.write({"langs_ready": True})
        self.entry.invalidate_recordset()
        self.assertTrue(self.entry.langs_ready)

    def test_cocher_relues_laisse_une_trace_au_chatter(self):
        before = len(self.entry.message_ids)
        self.entry.write({"langs_ready": True})
        self.entry.invalidate_recordset()
        self.assertEqual(len(self.entry.message_ids), before + 1)
        self.assertIn("Relue", self.entry.message_ids[0].body)

    def test_cocher_relues_sans_langue_exigee_ne_fait_rien(self):
        self.calendar.write({"require_all_langs": "no", "lang_ids": [(5, 0, 0)]})
        self.calendar.website_id = False
        self.entry.invalidate_recordset()
        self.assertTrue(self.entry.langs_ready)
        self.entry.write({"langs_ready": True})
        self.entry.invalidate_recordset()
        self.assertEqual(self.fr_slot.state, "todo")
        self.assertEqual(self.en_slot.state, "translated")

    # --- « Toutes les langues livrées » ----------------------------------
    def test_cocher_livrees_publie_les_creneaux_exiges(self):
        self.assertFalse(self.entry.langs_complete)
        self.entry.write({"langs_complete": True})
        self.entry.invalidate_recordset()
        self.assertEqual(
            set((self.fr_slot | self.en_slot).mapped("state")), {"published"})
        self.assertTrue(self.entry.langs_complete)
        self.assertTrue(self.entry.langs_ready)
        self.assertFalse(self.entry.published_date,
                         "cocher « livrées » ne publie pas l'entrée")

    def test_cocher_livrees_refuse_a_la_redaction_pour_la_bonne_raison(self):
        entry = self.entry.with_user(self.redaction)
        with self.assertRaises(AccessError) as caught:
            entry.write({"langs_complete": True})
        # Le message d'Odoo nomme aussi les groupes : il faut la phrase
        # propre à CETTE garde, sinon un test vert ne prouve rien.
        self.assertIn("Toutes les langues livrées", str(caught.exception))
        self.entry.invalidate_recordset()
        self.assertEqual(self.fr_slot.state, "todo")

    def test_decocher_livrees_ramene_a_relue(self):
        (self.fr_slot | self.en_slot).write({"state": "published"})
        self.entry.invalidate_recordset()
        self.assertTrue(self.entry.langs_complete)
        self.entry.write({"langs_complete": False})
        self.entry.invalidate_recordset()
        self.assertEqual(
            set((self.fr_slot | self.en_slot).mapped("state")), {"reviewed"})
        self.assertFalse(self.entry.langs_complete)
        self.assertTrue(self.entry.langs_ready,
                         "décocher « livrées » ne défait pas la relecture")
