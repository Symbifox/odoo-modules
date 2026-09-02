# -*- coding: utf-8 -*-
"""Un billet qui annonce un article doit en porter le lien.

Le connecteur Bluesky savait depuis toujours faire une carte de lien à partir
de ``link_url``, et les champs UTM du canal existaient. Rien ne les
renseignait : le premier billet parti en production, le 2026-08-29, annonçait
un article sans donner d'endroit où le lire.
"""

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestLienDiffuse(TransactionCase):

    def setUp(self):
        super().setUp()
        self.site = self.env["website"].search([], limit=1)
        self.fr = self.site.default_lang_id or self.env["res.lang"].search(
            [("active", "=", True)], limit=1)
        self.blog = self.env["blog.blog"].create({"name": "Blogue d'essai"})
        self.billet = self.env["blog.post"].create({
            "name": "Article d'essai", "blog_id": self.blog.id,
            "content": "<p>Du contenu.</p>",
        })
        self.cal = self.env["bf.editorial.calendar"].create({
            "name": "Diffusion", "require_all_langs": "no", "word_floor": 0,
            "website_id": self.site.id,
        })
        self.entry = self.env["bf.editorial.entry"].create({
            "name": "Article d'essai", "calendar_id": self.cal.id,
            "post_id": self.billet.id, "qa_state": "clean",
        })
        self.entry.checklist_ids.unlink()
        self.canal = self.env["bf.social.channel"].create({
            "name": "Canal d'essai", "network": "bluesky",
            "handle": "essai.test", "lang_id": self.fr.id, "login": "essai.test",
        })

    def _blurb(self, **kw):
        vals = {"entry_id": self.entry.id, "channel_id": self.canal.id,
                "body": "Une accroche courte."}
        vals.update(kw)
        return self.env["bf.editorial.blurb"].create(vals)

    # ── Résolution de l'URL ──────────────────────────────────────────────
    def test_url_absolue_et_sur_le_bon_domaine(self):
        url = self._blurb()._article_url()
        self.assertTrue(url.startswith("http"), url)
        self.assertIn(self.billet.website_url, url)

    def test_langue_par_defaut_sans_prefixe(self):
        """La langue du site ne porte pas de préfixe ; les autres, oui."""
        url = self._blurb()._article_url()
        self.assertNotIn("/%s/blog" % (self.fr.url_code or ""), url)

    def test_langue_secondaire_prefixee(self):
        autre = self.env["res.lang"].search(
            [("active", "=", True), ("id", "!=", self.fr.id)], limit=1)
        if not autre:
            self.skipTest("une seule langue active sur cette base")
        self.canal.lang_id = autre
        url = self._blurb()._article_url()
        self.assertIn("/%s/" % autre.url_code, url)

    def test_sans_billet_pas_d_url(self):
        self.entry.post_id = False
        self.assertFalse(self._blurb()._article_url())

    # ── Lien suivi ───────────────────────────────────────────────────────
    def test_mise_en_file_pose_le_lien(self):
        """Le point du correctif : action_queue renseignait tout sauf le lien."""
        billet_social = self._blurb().action_queue()
        self.assertTrue(billet_social.link_url, "un billet doit porter son lien")
        self.assertTrue(billet_social.tracker_id, "et son lien suivi")

    def test_le_lien_porte_les_utm_du_canal(self):
        source = self.env["utm.source"].create({"name": "Essai source"})
        medium = self.env["utm.medium"].create({"name": "Essai médium"})
        self.canal.write({"utm_source_id": source.id, "utm_medium_id": medium.id})
        traceur = self._blurb().action_queue().tracker_id
        self.assertEqual(traceur.source_id, source)
        self.assertEqual(traceur.medium_id, medium)

    def test_deux_blurbs_reutilisent_le_meme_traceur(self):
        """La contrainte d'unicité de link.tracker lèverait sur un doublon."""
        a = self._blurb(variant="A").action_queue().tracker_id
        b = self._blurb(variant="B").action_queue().tracker_id
        self.assertEqual(a, b)

    # ── Garde avant envoi ────────────────────────────────────────────────
    def test_envoi_refuse_sans_lien(self):
        post = self.env["bf.social.post"].create({
            "entry_id": self.entry.id, "channel_id": self.canal.id,
            "body": "Sans lien.", "kind": "new",
        })
        self.assertTrue(any("lien" in r for r in post._blocking_reasons()))
        with self.assertRaises(UserError):
            post.action_send_now()

    def test_adhoc_echappe_a_l_exigence(self):
        """Un billet ad hoc ne parle pas forcément d'un article."""
        post = self.env["bf.social.post"].create({
            "entry_id": self.entry.id, "channel_id": self.canal.id,
            "body": "Une pensée du jour.", "kind": "adhoc",
        })
        self.assertFalse(any("lien" in r for r in post._blocking_reasons()))


@tagged("post_install", "-at_install")
class TestCanalMultilingue(TransactionCase):
    """Un même compte se tient en plusieurs langues.

    Une page LinkedIn est la même page en français et en anglais. La contrainte
    d'unicité portait sur (réseau, pseudonyme, société) et interdisait donc le
    second canal — alors que le module exige une entrée par langue publiée.
    """

    def setUp(self):
        super().setUp()
        self.langues = self.env["res.lang"].search([("active", "=", True)], limit=2)
        if len(self.langues) < 2:
            self.skipTest("une seule langue active sur cette base")

    def _canal(self, langue, **kw):
        vals = {"name": "Page %s" % langue.code, "network": "bluesky",
                "handle": "meme-page", "lang_id": langue.id, "login": "x"}
        vals.update(kw)
        return self.env["bf.social.channel"].create(vals)

    def test_deux_langues_sur_le_meme_pseudonyme(self):
        a = self._canal(self.langues[0])
        b = self._canal(self.langues[1])
        self.assertNotEqual(a, b)
        self.assertEqual(a.handle, b.handle)

    def test_le_doublon_exact_reste_refuse(self):
        """Relâcher la contrainte ne doit pas la supprimer."""
        from psycopg2 import IntegrityError
        from odoo.tools import mute_logger
        self._canal(self.langues[0])
        with self.assertRaises(IntegrityError), mute_logger("odoo.sql_db"):
            with self.env.cr.savepoint():
                self._canal(self.langues[0])
