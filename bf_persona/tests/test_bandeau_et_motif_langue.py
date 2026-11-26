"""Le bandeau du composeur et le motif des cartes suivent la langue du lecteur.

Avant la 18.0.3.3.2, le bandeau écrivait en dur, hors `_()`, « Le brouillon
utilise… », « Copie obligatoire manquante : », « … et N autre(s) », le « vous »
après chaque nom ; un usager anglophone lisait ces phrases en français. Le motif
des cartes du tableau (« 3 courriels sans réponse depuis le… ») était celui que
le travail planifié avait ÉCRIT, dans une seule langue.

Chaque essai lit les DEUX langues : un essai en anglais seul passerait aussi
avec un catalogue vide, un essai en français seul avec la source d'avant.
"""

from datetime import timedelta

from odoo import fields
from odoo.tests import tagged

from .common import PersonaCase


@tagged("post_install", "-at_install")
class TestBandeauEtMotifLangue(PersonaCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["res.lang"]._activate_lang("en_US")
        cls.p_eli = cls.Persona.create({
            "partner_id": cls.eli.id, "addressing_style": "vous",
            "preferred_salutation": "Bonjour Ada", "closing_formula": "Cordialement",
        })
        cls.Rule = cls.env["contact.cc.rule"]

    def hint(self, lang, to, cc=(), body="<p>x</p>"):
        env = self.env(context=dict(self.env.context, lang=lang))
        composer = env["mail.compose.message"].create({
            "composition_mode": "comment",
            "model": "res.partner",
            "res_ids": str([to[0].id]),
            "partner_ids": [(6, 0, [p.id for p in to])],
            "partner_cc_ids": [(6, 0, [p.id for p in cc])],
            "body": body,
        })
        return str(composer.persona_hint_html)

    # --- le bandeau ---------------------------------------------------------

    def test_registre_salutation_et_cloture_dans_les_deux_langues(self):
        anglais = self.hint("en_US", [self.eli])
        self.assertIn("<b>Ada Exemple</b>: formal (vous)", anglais)
        self.assertIn("“Bonjour Ada”", anglais)
        self.assertIn("sign-off “Cordialement”", anglais)
        self.assertNotIn("clôture", anglais)
        self.assertNotIn(" : ", anglais)

        francais = self.hint("fr_CA", [self.eli])
        self.assertIn("<b>Ada Exemple</b> : vous", francais)
        self.assertIn("« Bonjour Ada »", francais)
        self.assertIn("clôture « Cordialement »", francais)
        self.assertNotIn("formal", francais)

    def test_le_tutoiement_se_nomme_aussi(self):
        self.p_eli.addressing_style = "tu"
        self.assertIn("</b>: informal (tu)", self.hint("en_US", [self.eli]))
        self.assertIn("</b> : tu", self.hint("fr_CA", [self.eli]))

    def test_mauvais_registre_dans_les_deux_langues(self):
        body = "<p>Tu as ton dossier, et ta facture est dans tes courriels.</p>"
        anglais = self.hint("en_US", [self.eli], body=body)
        self.assertIn("The draft says “tu” but this contact prefers “vous”.", anglais)
        self.assertNotIn("Le brouillon", anglais)
        self.assertIn("Le brouillon utilise « tu » alors que ce contact préfère le vouvoiement.",
                      self.hint("fr_CA", [self.eli], body=body))

        self.p_eli.addressing_style = "tu"
        body = "<p>Vous avez votre dossier, et vos factures.</p>"
        self.assertIn("The draft says “vous” but this contact prefers “tu”.",
                      self.hint("en_US", [self.eli], body=body))
        self.assertIn("Le brouillon utilise « vous » alors que ce contact préfère le tutoiement.",
                      self.hint("fr_CA", [self.eli], body=body))

    def test_regles_de_copie_dans_les_deux_langues(self):
        self.Rule.create({"persona_id": self.p_eli.id, "cc_partner_ids": [(6, 0, self.hm.ids)],
                          "mandatory": True})
        self.assertIn("Mandatory copy missing:</b> Clara Exemple", self.hint("en_US", [self.eli]))
        self.assertIn("Copie obligatoire manquante :</b> Clara Exemple", self.hint("fr_CA", [self.eli]))

    def test_regle_habituelle_et_interdit_dans_les_deux_langues(self):
        self.Rule.create({"persona_id": self.p_eli.id, "cc_partner_ids": [(6, 0, self.hm.ids)],
                          "mandatory": False})
        self.assertIn("Usually included:</b> Clara Exemple", self.hint("en_US", [self.eli]))
        self.assertIn("Habituellement inclus :</b> Clara Exemple", self.hint("fr_CA", [self.eli]))

        self.Rule.search([("persona_id", "=", self.p_eli.id)]).unlink()
        self.Rule.create({"persona_id": self.p_eli.id, "cc_partner_ids": [(6, 0, self.hm.ids)],
                          "rule_type": "never"})
        self.assertIn("Do not copy:</b> Clara Exemple", self.hint("en_US", [self.eli], cc=[self.hm]))
        self.assertIn("Ne pas mettre en copie :</b> Clara Exemple",
                      self.hint("fr_CA", [self.eli], cc=[self.hm]))

    def test_au_dela_de_quatre_destinataires(self):
        autres = self.Partner.create([
            {"name": "Exemple %s" % i, "email": "e%s@exemple.example" % i} for i in range(5)
        ])
        for partner in autres:
            self.Persona.create({"partner_id": partner.id})
        self.assertIn("… and 2 more", self.hint("en_US", [self.eli] + list(autres)))
        self.assertIn("… et 2 autre(s)", self.hint("fr_CA", [self.eli] + list(autres)))

    # --- le motif des cartes -------------------------------------------------

    def _sans_reponse(self):
        self.received(self.eli, days=40)
        for day in (30, 20, 10):
            self.sent([self.eli], days=day)
        self.p_eli._refresh_relationship_facts()
        self.assertEqual(self.p_eli.relationship_health, "watch")

    def test_le_motif_affiche_suit_la_langue_du_lecteur(self):
        self._sans_reponse()
        anglais = self.p_eli.with_context(lang="en_US").health_reason_display
        francais = self.p_eli.with_context(lang="fr_CA").health_reason_display
        self.assertIn("3 emails unanswered since", anglais)
        self.assertNotIn("sans réponse", anglais)
        self.assertIn("3 courriels sans réponse depuis le", francais)

    def test_le_motif_affiche_se_tait_quand_la_relation_va_bien(self):
        self._sans_reponse()
        self.assertTrue(self.p_eli.with_context(lang="en_US").health_reason_display)
        self.p_eli.relationship_health = "healthy"
        self.assertFalse(self.p_eli.with_context(lang="en_US").health_reason_display)

    def test_deux_signaux_se_separent_a_la_maniere_de_chaque_langue(self):
        """Le plus grave d'abord ; « ; » en français, « ; » sans espace avant en anglais."""
        self.patch(type(self.p_eli), "_health_signals",
                   lambda rec: [("watch", "A"), ("degraded", "B")])
        self.p_eli.relationship_health = "degraded"
        self.assertEqual(self.p_eli.with_context(lang="en_US").health_reason_display, "B; A")
        self.assertEqual(self.p_eli.with_context(lang="fr_CA").health_reason_display, "B ; A")

    def test_un_pont_qui_leve_ne_fait_pas_tomber_le_tableau(self):
        """Le motif retombe sur le texte stocké, comme le bandeau se tait sans planter."""
        def casse(rec):
            raise ValueError("pont cassé")
        self.patch(type(self.p_eli), "_health_signals", casse)
        self.p_eli.write({"relationship_health": "watch", "health_reason": "motif de la nuit"})
        self.assertEqual(self.p_eli.with_context(lang="en_US").health_reason_display, "motif de la nuit")

    def test_les_ecrans_lisent_le_motif_affiche(self):
        """Une vue qui relirait `health_reason` rendrait la langue du travail planifié."""
        for xmlid in ("bf_persona.view_contact_persona_kanban",):
            arch = self.env.ref(xmlid).arch_db
            self.assertIn('name="health_reason_display"', arch)
            self.assertNotIn('name="health_reason"', arch)
        for view in self.env["ir.ui.view"].search([("model", "=", "contact.persona")]):
            self.assertNotIn('name="health_reason"', view.arch_db, view.xml_id)

    # --- ce qui est donné à Gen ----------------------------------------------

    def test_gen_n_entend_pas_deux_fois_les_courriels_sans_reponse(self):
        """Le doublon se repérait aux mots « sans réponse » : en anglais, jamais."""
        self._sans_reponse()
        for lang in ("en_US", "fr_CA"):
            resume = self.p_eli.with_context(lang=lang)._build_claude_summary()
            self.assertNotIn("envoyé(s) sans réponse depuis", resume, lang)

    def test_gen_entend_les_courriels_sans_reponse_trop_recents_pour_un_signal(self):
        self.p_eli.write({
            "unanswered_count": 1,
            "unanswered_since": fields.Date.today() - timedelta(days=3),
        })
        resume = self.p_eli.with_context(lang="en_US")._build_claude_summary()
        self.assertIn("1 courriel(s) envoyé(s) sans réponse depuis", resume)
