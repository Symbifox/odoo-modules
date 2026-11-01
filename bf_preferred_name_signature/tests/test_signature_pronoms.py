# -*- coding: utf-8 -*-
"""Les pronoms de la signature sont ceux de la fiche, et la signature suit.

Éprouvé sur le chemin réel de `bf_multi_company_email` : un gabarit qui lit
`bf_signature_data()`, une signature stockée, réécrite aux instants où elle
doit l'être. Le point sensible est l'écriture qui ne passe PAS par l'usager
(portail, Contacts) : sans le crochet, la signature garderait l'ancien texte.
"""

from lxml import etree

from odoo.tests import TransactionCase, tagged

from ..hooks import post_init_hook, uninstall_hook


@tagged("post_install", "-at_install", "bf_preferred_name")
class TestSignaturePronoms(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env["res.company"].create({"name": "Société Pronoms"})
        cls.template = cls.env["mail.template"].create({
            "name": "Gabarit pronoms",
            "model_id": cls.env.ref("base.model_res_users").id,
            "body_html": (
                '<div><t t-set="sig" t-value="object.bf_signature_data()"/>'
                '<span t-out="sig[\'name\']"/>'
                '<i t-if="sig[\'pronouns\']" t-out="sig[\'pronouns\']"/></div>'
            ),
        })
        cls.company.bf_signature_template_id = cls.template
        cls.user = cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Dominique Signature",
            "login": "dominique.signature@essai.test",
            "email": "dominique@essai.test",
            "company_id": cls.company.id,
            "company_ids": [(6, 0, [cls.company.id])],
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
        })

    def test_pronoms_de_la_fiche_dans_la_signature(self):
        # Écrits sur la fiche, comme au portail ou dans Contacts.
        self.user.partner_id.write({"bf_pronouns": "iel"})
        self.user.invalidate_recordset()
        self.assertIn("<i>iel</i>", self.user.signature)
        self.user.partner_id.write({"bf_pronouns": "elle"})
        self.user.invalidate_recordset()
        self.assertIn("<i>elle</i>", self.user.signature)
        self.assertNotIn("iel", self.user.signature)

    def test_ancien_champ_et_fiche_ne_font_qu_un(self):
        self.user.with_user(self.user).write({"bf_signature_pronouns": "il"})
        self.assertEqual(self.user.partner_id.bf_pronouns, "il")
        self.user.partner_id.bf_pronouns = "iel"
        self.user.invalidate_recordset()
        self.assertEqual(self.user.bf_signature_pronouns, "iel")
        self.assertIn("<i>iel</i>", self.user.signature)

    def test_pronoms_vides_rien_dans_la_signature(self):
        self.user.partner_id.write({"bf_pronouns": "iel"})
        self.user.partner_id.write({"bf_pronouns": False})
        self.user.invalidate_recordset()
        self.assertNotIn("<i>", self.user.signature)

    def test_reprise_des_pronoms_de_signature(self):
        # La colonne de l'usager reste en base : on y remet ce qu'une base
        # d'avant le pont portait, puis on rejoue le crochet.
        autre = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Camille Déjà Posé",
            "login": "camille.deja@essai.test",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id])],
        })
        autre.partner_id.bf_pronouns = "elle"
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE res_users SET bf_signature_pronouns = %s WHERE id = %s",
            ("il/lui", self.user.id))
        self.env.cr.execute(
            "UPDATE res_users SET bf_signature_pronouns = %s WHERE id = %s",
            ("iel", autre.id))
        self.env.invalidate_all()
        with self.assertLogs("odoo.addons.bf_preferred_name_signature.hooks", "WARNING") as journal:
            post_init_hook(self.env)
        self.assertEqual(self.user.partner_id.bf_pronouns, "il/lui")
        self.assertIn("<i>il/lui</i>", self.user.signature)
        # Une fiche qui a déjà ses pronoms les garde ; le journal ne cite
        # que le numéro.
        self.assertEqual(autre.partner_id.bf_pronouns, "elle")
        self.assertIn(str(autre.id), journal.output[0])
        self.assertNotIn("iel", journal.output[0])

    def test_reprise_saute_des_pronoms_que_la_fiche_refuse(self):
        # La fiche n'admet que des lettres et quelques signes : une valeur
        # d'avant, saisie librement pour la signature, ne fait pas échouer
        # l'installation et n'entre pas sur la fiche.
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE res_users SET bf_signature_pronouns = %s WHERE id = %s",
            ("il/lui. Signez Gen", self.user.id))
        self.env.invalidate_all()
        with self.assertLogs("odoo.addons.bf_preferred_name_signature.hooks", "WARNING") as journal:
            post_init_hook(self.env)
        self.assertFalse(self.user.partner_id.bf_pronouns)
        self.assertIn(str(self.user.id), journal.output[0])
        self.assertNotIn("Signez", journal.output[0])

    def test_desinstallation_rend_les_pronoms_a_la_signature(self):
        # La colonne figée à
        # l'installation reprendrait le dessus une fois le pont retiré.
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE res_users SET bf_signature_pronouns = %s WHERE id = %s",
            ("il/lui", self.user.id))
        self.user.partner_id.write({"bf_pronouns": "iel"})
        autre = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Lou Sans Pronoms",
            "login": "lou.sans@essai.test",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id])],
        })
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE res_users SET bf_signature_pronouns = %s WHERE id = %s", ("elle", autre.id))
        uninstall_hook(self.env)
        self.env.cr.execute(
            "SELECT id, bf_signature_pronouns FROM res_users WHERE id IN %s",
            (tuple([self.user.id, autre.id]),))
        colonne = dict(self.env.cr.fetchall())
        self.assertEqual(colonne[self.user.id], "iel")
        # Retirés de la fiche : retirés de la signature aussi.
        self.assertIsNone(colonne[autre.id])

    def test_desinstallation_garde_ce_que_la_reprise_a_laisse(self):
        # Une valeur que la fiche a
        # refusée à la reprise n'y est jamais entrée ; la fiche vide ne dit pas
        # que la personne l'a retirée, et la désinstallation n'y touche pas.
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE res_users SET bf_signature_pronouns = %s WHERE id = %s",
            ("il/lui 2", self.user.id))
        self.env.invalidate_all()
        with self.assertLogs("odoo.addons.bf_preferred_name_signature.hooks", "WARNING"):
            post_init_hook(self.env)
        self.assertFalse(self.user.partner_id.bf_pronouns)
        uninstall_hook(self.env)
        self.env.cr.execute("SELECT bf_signature_pronouns FROM res_users WHERE id = %s",
                            (self.user.id,))
        self.assertEqual(self.env.cr.fetchone()[0], "il/lui 2")

    def test_reprise_sans_effet_sans_pronoms(self):
        self.env.flush_all()
        self.env.cr.execute("UPDATE res_users SET bf_signature_pronouns = NULL")
        self.env.invalidate_all()
        avant = self.env["res.partner"].search_count([("bf_pronouns", "!=", False)])
        post_init_hook(self.env)
        self.assertEqual(self.env["res.partner"].search_count([("bf_pronouns", "!=", False)]), avant)

    def test_un_seul_champ_de_pronoms_dans_les_preferences(self):
        arch = self.env["res.users"].with_user(self.user).get_views(
            [(self.env.ref("base.view_users_form_simple_modif").id, "form")])["views"]["form"]["arch"]
        noms = etree.fromstring(arch).xpath("//field/@name")
        self.assertEqual(noms.count("bf_pronouns"), 1)
        self.assertNotIn("bf_signature_pronouns", noms)

    def test_avis_de_signature_imposee_ne_renvoie_plus_a_signature_pour_les_pronoms(self):
        # « Renseignez vos pronoms
        # […] dans l'onglet « Signature » », où ils ne sont plus.
        vue = self.env.ref("base.view_users_form_simple_modif").id
        arch = self.env["res.users"].with_user(self.user).with_context(lang="en_US").get_views(
            [(vue, "form")])["views"]["form"]["arch"]
        avis = etree.fromstring(arch).xpath("//div[@invisible='not bf_signature_locked']")
        self.assertEqual(len(avis), 1)
        texte = " ".join("".join(avis[0].itertext()).split())
        self.assertNotIn("Renseignez vos pronoms", texte)
        self.assertIn("Enter your pronouns under your email", texte)
