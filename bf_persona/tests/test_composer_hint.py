from datetime import timedelta

from odoo import fields
from odoo.tests import tagged

from .common import PersonaCase


@tagged("post_install", "-at_install")
class TestComposerHint(PersonaCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.zoe = cls.Partner.create({"name": "Zoé Exemple", "email": "zoe@example.com"})
        cls.persona = cls.Persona.create({
            "partner_id": cls.zoe.id,
            "addressing_style": "tu",
            "preferred_salutation": "Bonjour <Zoé> & cie",
            "closing_formula": "Merci <b>",
        })

    def test_hint_tags_render_as_markup(self):
        self.persona.write({
            "unanswered_count": 3,
            "unanswered_since": fields.Date.today() - timedelta(days=20),
            "our_words_median": 180,
        })
        hint = str(self.composer([self.zoe]).persona_hint_html)
        self.assertIn("<b>Zoé Exemple</b> : tu", hint)
        self.assertIn('<span style="color:#b06b00;">⚠ 3 courriels sans réponse depuis le', hint)
        self.assertIn('<span style="color:#b06b00;">⚠ nos derniers courriels font 180 mots de médiane</span>', hint)
        self.assertNotIn("&lt;b&gt;Zoé", hint, "a fragment of the hint was escaped whole")
        self.assertNotIn("&lt;span", hint, "a fragment of the hint was escaped whole")

    def test_hint_escapes_text_typed_on_the_persona(self):
        self.persona.custom_appellations = "<script>x</script>"
        hint = str(self.composer([self.zoe]).persona_hint_html)
        self.assertIn("« Bonjour &lt;Zoé&gt; &amp; cie »", hint)
        self.assertIn("clôture « Merci &lt;b&gt; »", hint)
        self.assertIn("&lt;script&gt;", hint)
        self.assertNotIn("<Zoé>", hint)
        self.assertNotIn("<script>", hint)

    def test_register_warning_ignores_words_starting_like_tu(self):
        self.persona.addressing_style = "vous"
        body = "<p>Voici le tableau des taux, tant pis pour le test. Vous verrez votre dossier.</p>"
        hint = str(self.composer([self.zoe], body=body).persona_hint_html)
        self.assertNotIn("préfère le vouvoiement", hint)

    def test_register_warning_still_fires_on_real_tutoiement(self):
        self.persona.addressing_style = "vous"
        body = "<p>Tu as ton dossier, et ta facture est dans tes courriels.</p>"
        hint = str(self.composer([self.zoe], body=body).persona_hint_html)
        self.assertIn("préfère le vouvoiement", hint)

    def test_apply_persona_escapes_salutation_and_closing(self):
        composer = self.composer([self.zoe], body="<p>Le <i>corps</i> reste du HTML.</p>")
        composer.action_apply_persona()
        body = str(composer.body)
        self.assertIn("Bonjour &lt;Zoé&gt; &amp; cie,", body)
        self.assertIn("Merci &lt;b&gt;,", body)
        self.assertIn("<i>corps</i>", body)

    def test_long_messages_are_flagged(self):
        self.persona.our_words_median = 170
        hint = str(self.composer([self.zoe]).persona_hint_html)
        self.assertIn("170 mots de médiane", hint)
        self.persona.our_words_median = 60
        hint = str(self.composer([self.zoe]).persona_hint_html)
        self.assertNotIn("mots de médiane", hint)
