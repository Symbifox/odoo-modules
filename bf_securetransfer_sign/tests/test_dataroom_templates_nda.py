"""L'entente dans un préréglage : ce que le pont ajoute, et ce qu'il retire.

Le socle écrit `nda_required` sans rien savoir de bf_sign — il boucle sur ce
que `_apply_vals` lui rend. Ce que le pont doit tenir, c'est donc l'inverse :
REFUSER une exigence que la marque ne peut pas honorer, et le dire.

⚠ Le silence est le défaut coûteux ici. Une entente exigée sur une marque sans
document ne casse rien à l'application du préréglage : elle fait échouer
`action_send` tout à la fin, sur une case que l'expéditeur ne regarde plus.
"""
import base64
import io

from odoo.tests import TransactionCase, tagged


def _pdf_bytes():
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    doc = canvas.Canvas(buf)
    doc.drawString(72, 720, "ENTENTE")
    doc.showPage()
    doc.save()
    return buf.getvalue()


@tagged("post_install", "-at_install")
class TestDataroomTemplateNda(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Brand = cls.env["secure.transfer.brand"]
        cls.brand_nda = Brand.create({
            "name": "QA — marque avec entente",
            "allow_open_audience": True,
            "allow_audience_sms": True,
            "audience_max_default": 25,
            "nda_document": base64.b64encode(_pdf_bytes()),
            "nda_filename": "entente.pdf",
        })
        cls.brand_bare = Brand.create({
            "name": "QA — marque sans entente",
            "allow_open_audience": True,
            "audience_max_default": 25,
        })
        cls.Template = cls.env["secure.transfer.template"]
        cls.Wizard = cls.env["secure.transfer.send.wizard"]

    def test_seeded_templates_require_the_nda(self):
        """Une salle de données livrée s'ouvre derrière une entente signée."""
        for xmlid in ("template_dataroom_standard",
                      "template_dataroom_wide",
                      "template_dataroom_restricted"):
            tmpl = self.env.ref("bf_securetransfer.%s" % xmlid)
            self.assertTrue(
                tmpl.nda_required,
                "« %s » sort sans entente — le pont n'a pas posé son champ, "
                "ni par le fichier de données ni par la migration." % tmpl.name)

    def test_apply_vals_carries_the_nda_flag(self):
        tmpl = self.Template.create({"name": "QA — avec entente",
                                     "nda_required": True})
        self.assertTrue(tmpl._apply_vals()["nda_required"])

    def test_template_sets_nda_on_the_wizard(self):
        wizard = self.Wizard.new({"brand_id": self.brand_nda.id})
        tmpl = self.Template.create({
            "name": "QA — entente posée",
            "brand_id": self.brand_nda.id,
            "audience_mode": "open",
            "nda_required": True,
        })
        wizard.template_id = tmpl
        wizard._onchange_template_id()
        self.assertTrue(wizard.nda_required)
        self.assertEqual(wizard.audience_mode, "open")

    def test_nda_is_dropped_and_announced_when_the_brand_has_none(self):
        """Le cas qui compte : l'exigence tombe, et l'expéditeur l'apprend
        MAINTENANT plutôt qu'au refus final de l'envoi."""
        wizard = self.Wizard.new({"brand_id": self.brand_bare.id})
        tmpl = self.Template.create({
            "name": "QA — entente impossible",
            "audience_mode": "open",
            "nda_required": True,
        })
        wizard.template_id = tmpl
        res = wizard._onchange_template_id()
        self.assertFalse(wizard.nda_required)
        self.assertTrue(res and res.get("warning"),
                        "le retrait de l'entente doit remonter")
        self.assertIn("entente", res["warning"]["message"].lower())

    def test_nda_removes_the_sms_channel(self):
        """Une signature exige une adresse courriel. `action_send` le refuse
        déjà ; le préréglage doit le régler un écran plus tôt."""
        wizard = self.Wizard.new({"brand_id": self.brand_nda.id})
        tmpl = self.Template.create({
            "name": "QA — entente + SMS",
            "brand_id": self.brand_nda.id,
            "audience_mode": "open",
            "nda_required": True,
        })
        # Le préréglage lui-même interdit SMS + liste de domaines, pas SMS +
        # entente : c'est la marque qui offre le canal, et c'est ici que la
        # contradiction se règle.
        tmpl.audience_allow_sms = True
        wizard.template_id = tmpl
        res = wizard._onchange_template_id()
        self.assertFalse(wizard.audience_allow_sms)
        self.assertTrue(res and res.get("warning"))
