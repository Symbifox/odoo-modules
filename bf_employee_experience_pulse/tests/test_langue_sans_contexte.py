"""Le libellé stocké d'un segment se calcule dans la langue de la société quand le travail
planifié n'en a pas : la colonne « Équipe » des scores passait à « The whole company »."""
import ast

from odoo.tests import TransactionCase, tagged
from odoo.tools import file_open

from .common import PulseCase


@tagged("post_install", "-at_install")
class TestLangueSansContexte(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["res.lang"]._activate_lang("fr_CA")
        cls.env["ir.module.module"]._load_module_terms(
            ["bf_employee_experience_pulse"], ["fr_CA"], overwrite=True)

    def test_le_segment_societe_suit_la_langue_de_la_societe(self):
        """Le libellé se lit par la sélection traduite, dans la langue de l'appel : le cron, qui
        n'en a pas, fixe celle de la société (essai suivant)."""
        cle = "company-%s" % self.env.company.id
        fr = self.env(context={"lang": "fr_CA"})["bf.ex.pulse.score"]._segment_labels([cle])
        self.assertEqual(fr[cle], "Toute la société")
        en = self.env(context={"lang": "en_US"})["bf.ex.pulse.score"]._segment_labels([cle])
        self.assertEqual(en[cle], "The whole company")

    def test_le_cron_et_le_refus_lisent_la_langue(self):
        with file_open("bf_employee_experience_pulse/models/pulse_campaign.py") as f:
            arbre = ast.parse(f.read())
        cron = [n for n in ast.walk(arbre) if isinstance(n, ast.FunctionDef) and n.name == "_cron_flush_and_score"]
        self.assertIn("if not self.env.lang:", ast.unparse(cron[0]))
        ouvrir = [n for n in ast.walk(arbre) if isinstance(n, ast.FunctionDef) and n.name == "action_open"]
        self.assertIn("_description_selection", ast.unparse(ouvrir[0]))


@tagged("post_install", "-at_install")
class TestInvitationDansLaLangueDeLaPersonne(PulseCase):
    """Le gabarit d'invitation n'a pas de langue : elle sortait dans celle de qui l'envoie."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["res.lang"]._activate_lang("fr_CA")
        cls.env["res.lang"]._activate_lang("en_US")
        cls.env["ir.module.module"]._load_module_terms(
            ["bf_employee_experience_pulse"], ["fr_CA"], overwrite=True)

    def test_l_invitation_suit_la_societe_et_non_l_expediteur(self):
        self.company.partner_id.lang = "fr_CA"
        self.employees.work_contact_id.write({"lang": False})
        campaign = self._campaign()
        campaign.action_open()
        avant = self.env["mail.mail"].search([]).ids
        campaign.with_context(lang="en_US").action_send_invitations()
        sujets = self.env["mail.mail"].search([("id", "not in", avant)]).mapped("subject")
        self.assertTrue(sujets, "aucune invitation partie : l'essai ne mesure rien")
        self.assertTrue(all(s == "Deux minutes pour dire comment ça va" for s in sujets), sujets)
