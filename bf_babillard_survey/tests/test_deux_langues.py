"""Le pont parle anglais à l'anglophone et français au francophone.

Depuis 18.0.1.1.1, la source est anglaise et le français vient de fr_CA.po. Les
autres essais ne prouvent que la source : un catalogue vide ou abîmé les laisse
passer. Ceux-ci lisent le français que la base et le catalogue servent, par les
trois chemins du module : la vue (le bouton), un refus (`_()` dans la langue de
qui publie) et la carte (`env._` dans la langue de la maison).
"""
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestDeuxLangues(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["res.lang"]._activate_lang("fr_CA")
        cls.env["res.lang"]._activate_lang("en_CA")
        # 🔴 Le français du bouton vit EN BASE (arch_db), posé par la montée : un
        # catalogue abîmé ne l'en retire pas, et l'essai passerait sur la base d'hier.
        # On l'efface donc (transaction de l'essai, annulée à la fin) avant de recharger
        # le catalogue LIVRÉ : c'est lui que l'essai lit, comme sur une base neuve.
        vue = cls.env.ref("bf_babillard_survey.view_survey_form_babillard")
        cls.env.cr.execute(
            "UPDATE ir_ui_view SET arch_db = arch_db - 'fr_CA' WHERE id = %s", [vue.id])
        vue.invalidate_recordset(["arch_db"])
        cls.env["ir.module.module"]._load_module_terms(
            ["bf_babillard_survey"], ["fr_CA"], overwrite=True)
        # La vue combinée se garde en cache par langue : sans ceci, un rendu fr_CA
        # d'avant l'effacement servirait encore.
        cls.env.registry.clear_all_caches()
        Users = cls.env["res.users"].with_context(no_reset_password=True)
        cls.u_redactrice = Users.create({
            "name": "Rédactrice bilingue", "login": "pont_survey_bilingue",
            "email": "pont.survey.bilingue@exemple.test",
            "groups_id": [(6, 0, [
                cls.env.ref("base.group_user").id,
                cls.env.ref("bf_babillard.group_babillard_redacteur").id,
                cls.env.ref("survey.group_survey_user").id])]})

    def _sondage(self, **kw):
        vals = {"title": "Bilan de l'automne", "access_mode": "public"}
        vals.update(kw)
        return self.env["survey.survey"].with_user(self.u_redactrice).create(vals)

    def test_le_bouton_suit_la_langue_de_qui_lit(self):
        Survey = self.env["survey.survey"].with_user(self.u_redactrice)
        for lang, attendu, absent in (
                ("fr_CA", "Annoncer au babillard", "Announce on the noticeboard"),
                ("en_US", "Announce on the noticeboard", "Annoncer au babillard"),
                ("en_CA", "Announce on the noticeboard", "Annoncer au babillard")):
            arch = Survey.with_context(lang=lang).get_views(
                [(False, "form")])["views"]["form"]["arch"]
            self.assertIn(attendu, arch, lang)
            self.assertNotIn(absent, arch, lang)

    def test_le_refus_suit_la_langue_de_qui_publie(self):
        sondage = self._sondage(access_mode="token")
        for lang, attendu in (("fr_CA", "réservé aux personnes invitées"),
                              ("en_US", "restricted to invited people"),
                              ("en_CA", "restricted to invited people")):
            with self.assertRaises(UserError) as refus:
                sondage.with_user(self.u_redactrice).with_context(
                    lang=lang).action_annoncer_au_babillard()
            self.assertIn(attendu, str(refus.exception), lang)

    def test_la_carte_suit_la_langue_de_la_maison(self):
        """La carte est lue par tous : elle s'écrit dans la langue de la société,
        pas dans celle de la session qui publie (bf_babillard, _langue_de_la_maison)."""
        partenaire = self.env.company.partner_id
        for maison, attendu in (("fr_CA", "Répondre au sondage"),
                                ("en_CA", "Take the survey")):
            partenaire.lang = maison
            sondage = self._sondage(title=f"Sondage {maison}")
            action = sondage.with_user(self.u_redactrice).with_context(
                lang="en_US" if maison == "fr_CA" else "fr_CA").action_annoncer_au_babillard()
            carte = self.env["bf.babillard.post"].sudo().browse(action["res_id"])
            self.assertIn(attendu, carte.corps_html, maison)
