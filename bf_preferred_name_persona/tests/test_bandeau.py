# -*- coding: utf-8 -*-
"""Le bandeau du composeur dit les pronoms des destinataires.

Ce qui est éprouvé sort de la forme de `bf_persona` : un persona de SOCIÉTÉ
couvre ses personnes (le fait doit venir de la personne, pas de la société),
un destinataire sans persona n'a pas de bandeau du tout (il doit en avoir un
s'il a donné ses pronoms), et tout ce qui est saisi est échappé.
"""

from lxml import etree

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "bf_preferred_name")
class TestBandeauPronoms(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Partner = cls.env["res.partner"]
        cls.societe = Partner.create({"name": "Société Témoin", "is_company": True})
        cls.env["contact.persona"].create({
            "partner_id": cls.societe.id, "addressing_style": "vous"})
        cls.alex = Partner.create({
            "name": "Alex Employé", "email": "alex@essai.test", "parent_id": cls.societe.id,
            "bf_pronouns": "iel", "bf_grammatical_gender": "neuter"})
        cls.sam = Partner.create({
            "name": "Sam Sans Persona", "email": "sam@essai.test", "bf_grammatical_gender": "feminine"})
        cls.lou = Partner.create({"name": "Lou Rien Donné", "email": "lou@essai.test"})
        cls.libelle = dict(Partner._fields["bf_grammatical_gender"]._description_selection(cls.env))

    def composer(self, to=(), cc=()):
        return self.env["mail.compose.message"].create({
            "composition_mode": "comment",
            "model": "res.partner",
            "res_ids": str([(to or cc)[0].id]),
            "partner_ids": [(6, 0, [p.id for p in to])],
            "partner_cc_ids": [(6, 0, [p.id for p in cc])],
            "body": "<p>x</p>",
        })

    def test_personne_couverte_par_le_persona_de_sa_societe(self):
        bandeau = str(self.composer([self.alex]).persona_hint_html)
        self.assertIn("<b>Société Témoin</b> : vous", bandeau)
        self.assertIn("<b>Alex Employé</b> : iel · %s" % self.libelle["neuter"], bandeau)

    def test_destinataire_sans_persona_a_son_bandeau(self):
        bandeau = str(self.composer([self.sam]).persona_hint_html)
        self.assertIn("<b>Sam Sans Persona</b> : %s" % self.libelle["feminine"], bandeau)

    def test_en_copie_aussi(self):
        bandeau = str(self.composer([self.lou], cc=[self.sam]).persona_hint_html)
        self.assertIn("Sam Sans Persona", bandeau)

    def test_rien_pour_qui_n_a_rien_donne(self):
        self.assertFalse(self.composer([self.lou]).persona_hint_html)

    def test_pronoms_et_nom_echappes(self):
        # Les pronoms n'admettent plus de chevrons (bf_preferred_name) : une
        # valeur d'avant la contrainte est posée en SQL. Le nom, lui, est libre.
        self.sam.name = "Sam <img src=x onerror=alert(1)>"
        self.env.flush_all()
        self.env.cr.execute("UPDATE res_partner SET bf_pronouns = %s WHERE id = %s",
                            ("<script>x</script>", self.sam.id))
        self.sam.invalidate_recordset()
        bandeau = str(self.composer([self.sam]).persona_hint_html)
        self.assertIn("&lt;script&gt;", bandeau)
        self.assertNotIn("<script>", bandeau)
        self.assertIn("&lt;img", bandeau)
        self.assertNotIn("<img", bandeau)

    def test_bandeau_suit_les_destinataires(self):
        composer = self.composer([self.lou])
        self.assertFalse(composer.persona_hint_html)
        composer.partner_ids = [(4, self.sam.id)]
        self.assertIn("Sam Sans Persona", str(composer.persona_hint_html))

    def test_bouton_du_persona_seulement_s_il_y_a_un_persona(self):
        # Pronoms sans persona = un
        # bandeau, mais un bouton « Appliquer le persona » qui ne faisait rien.
        sans = self.composer([self.sam])
        self.assertTrue(sans.persona_hint_html)
        self.assertFalse(sans.persona_hint_partner_id)
        self.assertTrue(self.composer([self.alex]).persona_hint_partner_id)
        vue = self.env.ref("mail.email_compose_message_wizard_form").id
        arch = self.env["mail.compose.message"].get_views([(vue, "form")])["views"]["form"]["arch"]
        bouton = etree.fromstring(arch).xpath("//button[@name='action_apply_persona']")
        self.assertEqual(len(bouton), 1)
        self.assertEqual(bouton[0].get("invisible"), "not persona_hint_partner_id")

    def test_au_dela_de_cinq_le_reste_se_compte_dans_la_langue(self):
        # « … et %s autre(s) » était
        # écrit en dur, en français pour tout le monde.
        gens = self.env["res.partner"].create([
            {"name": f"Personne {i}", "email": f"p{i}@essai.test", "bf_pronouns": "iel"}
            for i in range(7)])
        composer = self.composer(list(gens))
        self.assertIn("… and 2 more", str(composer.with_context(lang="en_US")._bf_pronoun_lines()[-1]))
        if self.env["res.lang"]._lang_get("fr_CA"):
            self.assertIn("… et 2 autre(s)",
                          str(composer.with_context(lang="fr_CA")._bf_pronoun_lines()[-1]))
