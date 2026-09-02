# -*- coding: utf-8 -*-
"""Un canal manuel dit non, et il le dit utilement.

La tentation serait de le faire passer pour un canal comme les autres. Un
`_publish` qui ne lève pas laisserait croire à une diffusion qui n'a pas eu
lieu — exactement le « échec silencieux qui se relit comme une réussite »
que le contrat de connecteur interdit.
"""

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestCanalManuel(TransactionCase):

    def setUp(self):
        super().setUp()
        lang = self.env["res.lang"].search([("active", "=", True)], limit=1)
        self.cal = self.env["bf.editorial.calendar"].create({
            "name": "Diffusion", "require_all_langs": "no", "word_floor": 0,
        })
        self.entry = self.env["bf.editorial.entry"].create({
            "name": "Article d'essai", "calendar_id": self.cal.id,
            "qa_state": "clean",
        })
        self.entry.checklist_ids.unlink()
        self.canal = self.env["bf.social.channel"].create({
            "name": "LinkedIn — Blue Fox", "network": "linkedin_manual",
            "handle": "blue-fox", "lang_id": lang.id,
        })
        self.post = self.env["bf.social.post"].create({
            "entry_id": self.entry.id, "channel_id": self.canal.id,
            "body": "Une accroche.", "kind": "new",
            "link_url": "https://exemple.test/article",
        })

    def test_le_reseau_est_selectionnable(self):
        """Le cadre découvre les connecteurs installés : celui-ci doit y être."""
        reseaux = dict(self.env["bf.social.channel"]._selection_network())
        self.assertIn("linkedin_manual", reseaux)

    def test_la_limite_est_celle_de_linkedin(self):
        self.assertEqual(self.canal.body_limit, 3000)

    def test_la_diffusion_refuse_et_explique(self):
        """On éprouve `_publish` directement, pas `action_send_now`.

        `_claim_and_send` valide la transaction avant l'appel sortant — c'est
        sa réservation anti-doublon, et elle est juste — mais un `commit` est
        interdit dans un test. Le refus qu'on veut vérifier vit de toute façon
        dans le connecteur, pas dans la file.
        """
        with self.assertRaises(UserError) as caught:
            self.canal._connector()._publish(self.post)
        self.assertIn("manuel", str(caught.exception).lower())

    def test_identifiants_ni_valides_ni_refuses(self):
        """Ni « valide » ni « refusé » : il n'y a pas de session à ouvrir."""
        ok, message = self.canal._connector()._validate_credentials(self.canal)
        self.assertTrue(ok)
        self.assertIn("manuel", message.lower())

    # ── Consignation manuelle ────────────────────────────────────────────
    def test_consignation_sans_url_refuse(self):
        with self.assertRaises(UserError):
            self.post.action_mark_published_manually()

    def test_consignation_refuse_l_url_de_l_article(self):
        """Le piège que j'ai failli livrer : consigner le lien de l'article
        comme preuve de publication."""
        self.post.manual_url = self.post.link_url
        with self.assertRaises(UserError) as caught:
            self.post.action_mark_published_manually()
        self.assertIn("article", str(caught.exception).lower())

    def test_consignation_ferme_la_boucle(self):
        self.post.manual_url = "https://www.linkedin.com/feed/update/urn:li:share:1"
        self.post.action_mark_published_manually()
        self.assertEqual(self.post.state, "sent")
        self.assertTrue(self.post.published_datetime)
        self.assertEqual(self.post.remote_url, self.post.manual_url)

    def test_pas_de_double_consignation(self):
        self.post.manual_url = "https://www.linkedin.com/feed/update/urn:li:share:1"
        self.post.action_mark_published_manually()
        with self.assertRaises(UserError):
            self.post.action_mark_published_manually()

    def test_un_canal_api_refuse_la_consignation_manuelle(self):
        autre = self.env["bf.social.channel"].create({
            "name": "Bluesky", "network": "bluesky", "handle": "essai.test",
            "lang_id": self.canal.lang_id.id, "login": "essai.test",
        })
        p = self.env["bf.social.post"].create({
            "entry_id": self.entry.id, "channel_id": autre.id,
            "body": "x", "kind": "new", "link_url": "https://exemple.test/a",
        })
        p.manual_url = "https://bsky.app/x"
        with self.assertRaises(UserError):
            p.action_mark_published_manually()

    # ── Le lien doit être DANS le texte ──────────────────────────────────
    def test_le_connecteur_reclame_le_lien_dans_le_corps(self):
        self.assertTrue(self.canal._connector()._link_in_body())

    def test_la_mise_en_file_ecrit_le_lien_dans_le_texte(self):
        """Sur un canal manuel, ce qui part est ce qui est dans le corps.

        Un lien resté dans `link_url` ne serait jamais collé : le blurb
        arriverait sur LinkedIn en annonçant un article sans dire où le lire.
        """
        billet = self.env["blog.post"].create({
            "name": "Article", "blog_id": self.env["blog.blog"].create(
                {"name": "Blogue"}).id, "content": "<p>x</p>",
        })
        self.entry.post_id = billet.id
        blurb = self.env["bf.editorial.blurb"].create({
            "entry_id": self.entry.id, "channel_id": self.canal.id,
            "body": "Une accroche.", "hashtags": "#Odoo",
        })
        post = blurb.action_queue()
        self.assertTrue(post.link_url)
        self.assertIn(post.link_url, post.body,
                      "le lien doit figurer dans le texte collé")
        self.assertTrue(post.body.index(post.link_url)
                        < post.body.index("#Odoo"),
                        "le lien avant les mots-clics, comme on l'écrit à la main")

    def test_la_longueur_du_blurb_compte_le_lien(self):
        """Sinon un blurb au ras de la limite déborde une fois le lien ajouté."""
        billet = self.env["blog.post"].create({
            "name": "Article", "blog_id": self.env["blog.blog"].create(
                {"name": "Blogue"}).id, "content": "<p>x</p>",
        })
        self.entry.post_id = billet.id
        blurb = self.env["bf.editorial.blurb"].create({
            "entry_id": self.entry.id, "channel_id": self.canal.id,
            "body": "Court.",
        })
        self.assertGreater(blurb.body_length, len("Court."),
                           "la longueur doit réserver la place du lien")
