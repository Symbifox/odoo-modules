# -*- coding: utf-8 -*-
"""Ce que GenFox écrit en base se lit dans la langue de qui agit.

Le nom d'une proposition est stocké : il prend les libellés traduits du type,
pas ceux de la source. La note laissée à l'application passe par le catalogue.
"""

import json

from odoo.tests import TransactionCase, tagged

from odoo.addons.bf_editorial_genfox.models.suggestion import digest

from .test_genfox import EN, FR


@tagged("post_install", "-at_install")
class TestLangueGenFox(TransactionCase):

    def setUp(self):
        super().setUp()
        self.env.user.groups_id = [(4, self.env.ref(
            "bf_editorial.group_editorial_manager").id)]
        Lang = self.env["res.lang"]
        for code in ("fr_CA", "en_CA", "en_US"):
            Lang._activate_lang(code)
        blog = self.env["blog.blog"].create({"name": "Banc éditorial"})
        post = self.env["blog.post"].create({"name": "Billet d'essai", "blog_id": blog.id})
        self.env.cr.execute(
            "UPDATE blog_post SET content = %s::jsonb WHERE id = %s",
            (json.dumps({"fr_CA": FR, "en_CA": EN, "en_US": EN}), post.id),
        )
        post.invalidate_recordset(["content"])
        self.calendar = self.env["bf.editorial.calendar"].create({
            "name": "Flux GenFox", "require_all_langs": "no", "word_floor": 0,
        })
        self.entry = self.env["bf.editorial.entry"].create({
            "name": "Entrée d'essai", "calendar_id": self.calendar.id, "post_id": post.id,
        })
        self.env["bf.editorial.version"].create([
            {"entry_id": self.entry.id, "is_source": True,
             "lang_id": Lang.search([("code", "=", "fr_CA")], limit=1).id},
            {"entry_id": self.entry.id,
             "lang_id": Lang.search([("code", "=", "en_CA")], limit=1).id},
        ])
        self.entry.qa_state = "clean"

    def _suggestion(self, lang, **values):
        vals = {
            "kind": "full", "entry_id": self.entry.id, "calendar_id": self.calendar.id,
            "state": "done", "source_digest": digest(FR),
        }
        vals.update(values)
        return self.env["bf.editorial.suggestion"].with_context(lang=lang).create(vals)

    def test_le_nom_stocke_prend_le_libelle_traduit(self):
        self.assertTrue(self._suggestion("fr_CA").name.startswith("Revue Gen"))
        self.assertTrue(self._suggestion("en_US").name.startswith("Gen review"))

    def test_la_note_d_application_passe_par_le_catalogue(self):
        self._suggestion("fr_CA", proposed_fr="<p>Version étoffée.</p>").action_apply()
        fil = " ".join(c or "" for c in self.entry.message_ids.mapped("body"))
        self.assertIn("Proposition Gen appliquée par", fil)
        self.assertNotIn("Gen suggestion applied", fil)
