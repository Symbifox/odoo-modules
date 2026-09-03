"""Préréglages de salle de données : ce qu'ils posent, et ce qu'ils refusent.

Un préréglage n'ajoute aucun pouvoir — il rejoue des valeurs que l'assistant
d'envoi savait déjà appliquer. Sa valeur est donc entièrement dans deux
choses, et c'est elles qu'on tient ici :

* **il pose ce qu'il annonce** — tous les champs, jusqu'au bout, sans qu'un
  onchange du socle en efface un derrière lui ;
* **il refuse ce qui ne tiendrait pas** — une combinaison qui produirait des
  liens refusant leurs visiteurs un par un doit mourir à l'enregistrement,
  pas en production.

⚠ Le cas qui a motivé la suite : un préréglage en audience ouverte posé sur
une marque qui n'offre pas le mode. Le socle ramène le mode à « destinataires
nommés » — correctement — mais un retour SILENCIEUX redonnerait exactement le
lien mort-né que ce modèle existe pour éviter. On vérifie donc que
l'avertissement REMONTE.
"""
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged

from .common import LanguesActives


@tagged("post_install", "-at_install")
class TestDataroomTemplates(LanguesActives, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Brand = cls.env["secure.transfer.brand"]
        cls.brand_open = Brand.create({
            "name": "QA — salle de données",
            "allow_open_audience": True,
            "audience_max_default": 25,
        })
        cls.brand_plain = Brand.create({
            "name": "QA — marque ordinaire",
            "allow_open_audience": False,
        })
        cls.Template = cls.env["secure.transfer.template"]

    # ── Ce que le module livre ────────────────────────────────────────────────
    def test_seeded_templates_carry_no_brand(self):
        """Les trois préréglages livrés ne doivent porter AUCUNE marque.

        Le module est la source unique de quatre locataires : une marque semée
        ici n'existerait sur aucun d'eux, et le préréglage serait inutilisable
        partout."""
        for xmlid in ("template_dataroom_standard",
                      "template_dataroom_wide",
                      "template_dataroom_restricted"):
            tmpl = self.env.ref("bf_securetransfer.%s" % xmlid)
            self.assertFalse(
                tmpl.brand_id,
                "« %s » porte une marque : elle n'existera pas chez les autres "
                "locataires." % tmpl.name)
            self.assertEqual(tmpl.audience_mode, "open")
            self.assertTrue(tmpl.note, "un préréglage sans note ne s'explique pas")

    # ── Ce qu'un préréglage pose ──────────────────────────────────────────────
    def test_apply_vals_carries_every_knob(self):
        tmpl = self.Template.create({
            "name": "QA — complet",
            "brand_id": self.brand_open.id,
            "audience_mode": "open",
            "retention_days": 21,
            "audience_max": 40,
            "audience_max_downloads": 7,
            "notify_on_join": False,
        })
        vals = tmpl._apply_vals()
        self.assertEqual(vals["brand_id"], self.brand_open.id)
        self.assertEqual(vals["retention_days"], 21)
        self.assertEqual(vals["audience_max"], 40)
        self.assertEqual(vals["audience_max_downloads"], 7)
        self.assertFalse(vals["notify_on_join"])

    def test_apply_vals_omits_brand_when_unset(self):
        """Clé absente = « ne touche pas ». Une clé à False remettrait la
        marque à vide et casserait l'envoi."""
        tmpl = self.Template.create({"name": "QA — sans marque"})
        self.assertNotIn("brand_id", tmpl._apply_vals())

    def test_wizard_applies_template(self):
        wizard = self.env["secure.transfer.send.wizard"].new({
            "brand_id": self.brand_plain.id,
        })
        tmpl = self.Template.create({
            "name": "QA — appliqué",
            "brand_id": self.brand_open.id,
            "audience_mode": "open",
            "retention_days": 30,
            "audience_max": 25,
            "audience_max_downloads": 5,
        })
        wizard.template_id = tmpl
        wizard._onchange_template_id()
        self.assertEqual(wizard.brand_id, self.brand_open)
        self.assertEqual(wizard.audience_mode, "open")
        self.assertEqual(wizard.retention_days, 30)
        self.assertEqual(wizard.audience_max, 25)
        self.assertEqual(wizard.audience_max_downloads, 5)

    def test_wizard_warns_when_brand_cannot_honour_the_mode(self):
        """Le cœur de la suite : le repli doit être BRUYANT.

        Préréglage sans marque + marque courante qui n'offre pas l'audience
        ouverte. Le mode retombe — c'est correct — mais en silence il rendrait
        un lien qui n'admet personne."""
        wizard = self.env["secure.transfer.send.wizard"].new({
            "brand_id": self.brand_plain.id,
        })
        tmpl = self.Template.create({
            "name": "QA — mode non offert", "audience_mode": "open"})
        wizard.template_id = tmpl
        res = wizard._onchange_template_id()
        self.assertEqual(wizard.audience_mode, "declared")
        self.assertTrue(res and res.get("warning"),
                        "le repli du mode doit remonter un avertissement")
        self.assertIn("audience ouverte", res["warning"]["message"])

    # ── Ce qu'un préréglage refuse ────────────────────────────────────────────
    def test_open_mode_needs_a_brand_that_offers_it(self):
        with self.assertRaises(ValidationError):
            self.Template.create({
                "name": "QA — incohérent",
                "brand_id": self.brand_plain.id,
                "audience_mode": "open",
            })

    def test_sms_and_domain_allowlist_cannot_coexist(self):
        """`_audience_admissible` refuse déjà le SMS dès qu'une liste est
        posée. Un préréglage qui promet les deux ment à l'expéditeur."""
        with self.assertRaises(ValidationError):
            self.Template.create({
                "name": "QA — SMS + liste",
                "audience_allow_sms": True,
                "audience_domains": "@client.com",
            })

    def test_retention_must_be_positive(self):
        with self.assertRaises(Exception):
            self.Template.create({"name": "QA — zéro jour", "retention_days": 0})
