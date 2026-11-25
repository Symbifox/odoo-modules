"""Le pont parle anglais à l'anglophone et français au francophone.

Depuis 18.0.1.0.2, la source est anglaise et le français vient de fr_CA.po : ces
essais lisent le français servi par la vue (le bouton) et par la carte (langue de
la maison), et tombent si le catalogue livré ne le porte pas.
"""
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
        vue = cls.env.ref("bf_babillard_appointment_poll.view_appointment_poll_form_babillard")
        cls.env.cr.execute(
            "UPDATE ir_ui_view SET arch_db = arch_db - 'fr_CA' WHERE id = %s", [vue.id])
        vue.invalidate_recordset(["arch_db"])
        cls.env["ir.module.module"]._load_module_terms(
            ["bf_appointment_poll", "bf_babillard_appointment_poll"], ["fr_CA"], overwrite=True)
        # La vue combinée se garde en cache par langue : sans ceci, un rendu fr_CA
        # d'avant l'effacement servirait encore.
        cls.env.registry.clear_all_caches()
        Users = cls.env["res.users"].with_context(no_reset_password=True)
        cls.u_redactrice = Users.create({
            "name": "Rédactrice bilingue", "login": "pont_poll_bilingue",
            "email": "pont.poll.bilingue@exemple.test",
            "groups_id": [(6, 0, [
                cls.env.ref("base.group_user").id,
                cls.env.ref("bf_babillard.group_babillard_redacteur").id])]})
        cls.type_rdv = cls.env["resource.booking.type"].sudo().search([], limit=1) \
            or cls.env["resource.booking.type"].sudo().create({
                "name": "Rencontre du banc", "duration": 1.0})

    def test_le_bouton_suit_la_langue_de_qui_lit(self):
        Poll = self.env["appointment.poll"].with_user(self.u_redactrice)
        for lang, attendu, absent in (
                ("fr_CA", "Annoncer au babillard", "Announce on the noticeboard"),
                ("en_US", "Announce on the noticeboard", "Annoncer au babillard"),
                ("en_CA", "Announce on the noticeboard", "Annoncer au babillard")):
            arch = Poll.with_context(lang=lang).get_views(
                [(False, "form")])["views"]["form"]["arch"]
            self.assertIn(attendu, arch, lang)
            self.assertNotIn(absent, arch, lang)

    def test_la_carte_suit_la_langue_de_la_maison(self):
        partenaire = self.env.company.partner_id
        for maison, attendu in (("fr_CA", "cherche des réponses"),
                                ("en_CA", "is looking for answers")):
            partenaire.lang = maison
            poll = self.env["appointment.poll"].with_user(self.u_redactrice).create({
                "name": f"Comité {maison}", "user_id": self.u_redactrice.id,
                "type_id": self.type_rdv.id})
            poll.sudo().write({"state": "open"})
            action = poll.with_user(self.u_redactrice).with_context(
                lang="en_US" if maison == "fr_CA" else "fr_CA").action_annoncer_au_babillard()
            carte = self.env["bf.babillard.post"].sudo().browse(action["res_id"])
            self.assertIn(attendu, carte.corps_html, maison)
