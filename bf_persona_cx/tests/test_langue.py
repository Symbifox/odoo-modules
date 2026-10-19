"""La langue des lignes que le pont ajoute au résumé du persona.

La source du module est anglaise et le français vit dans `i18n/fr_CA.po`. Deux
lignes étaient écrites en dur en français, hors de `_()` : le résumé du dernier
retour d'expérience et la mention « ne pas solliciter ». La ligne d'expérience
est maintenant une phrase entière traduisible, assemblée par `_()`.

Les essais épinglent fr_CA et en_CA, qu'ils activent : selon la base, en_US est
absente ou seule, et l'administrateur d'une base réelle est en fr_CA. Le retour éprouvé est un simple
commentaire (sans note) : sa ligne ne tient alors qu'à des termes de ce module,
pas aux libellés de `bf_cx`.
"""
from datetime import timedelta

from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestLangue(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        for code in ("fr_CA", "en_CA"):
            cls.env["res.lang"]._activate_lang(code)
        cls.env["ir.module.module"]._load_module_terms(
            ["bf_persona_cx"], ["fr_CA"], overwrite=True)
        cls.partner = cls.env["res.partner"].create({
            "name": "Nadia Langue", "email": "nadia.langue@essai.invalid",
        })
        cls.persona = cls.env["contact.persona"].create({"partner_id": cls.partner.id})
        cls.date = fields.Date.today() - timedelta(days=5)
        cls.env["bf.cx.feedback"].create({
            "partner_id": cls.partner.id, "kind": "verbatim", "source": "manual",
            "score": 0.0, "score_max": 0.0, "comment": "Parfait",
            "date": cls.date,
        })

    def _lignes(self, lang):
        # Les champs cx_* ne sont pas stockés : relus frais, pas tirés d'un cache d'avant le retour.
        self.persona.invalidate_recordset()
        return self.persona.with_context(lang=lang)._summary_extra_lines()

    def test_la_ligne_d_experience_est_une_phrase_traduite(self):
        """La ligne du dernier retour se lit en entier dans la langue demandée.

        Avant le correctif, elle était assemblée en dur en français
        (« Expérience client : commentaire le … ») : un lecteur anglais la
        recevait en français.
        """
        jour = fields.Date.to_string(self.date)
        self.assertIn(
            "Customer experience: comment on %s: \"Parfait\"." % jour,
            self._lignes("en_CA"))
        self.assertIn(
            "Expérience client : commentaire le %s : « Parfait »." % jour,
            self._lignes("fr_CA"))
        self.assertNotIn("Expérience client", " ".join(self._lignes("en_CA")))
        self.assertNotIn("Customer experience", " ".join(self._lignes("fr_CA")))

    def test_la_mention_ne_pas_solliciter_suit_la_langue(self):
        """La mention d'exclusion se lit dans la langue demandée.

        Avant le correctif, elle était écrite en dur en français.
        """
        if "bf_cx_exclude" not in self.env["res.partner"]._fields:
            self.skipTest("ce bf_cx n'a pas de champ d'exclusion")
        self.partner.bf_cx_exclude = True
        self.persona.invalidate_recordset(["cx_excluded"])
        self.assertIn("Do not solicit (surveys, testimonials).", self._lignes("en_CA"))
        self.assertIn("Ne pas solliciter (sondages, témoignages).", self._lignes("fr_CA"))
        self.assertNotIn("Ne pas solliciter (sondages, témoignages).", self._lignes("en_CA"))

    def test_la_raison_de_sante_d_un_detracteur_suit_la_langue(self):
        """L'extrait du commentaire d'un détracteur se ponctue selon la langue.

        La raison de santé ajoutait l'extrait par « : « … » » en dur : un lecteur
        anglais recevait la ponctuation française. Elle passe maintenant par `_()`.
        """
        jour = fields.Date.today() - timedelta(days=2)
        self.env["bf.cx.feedback"].create({
            "partner_id": self.partner.id, "kind": "nps", "source": "manual",
            "score": 3.0, "score_max": 10.0, "comment": "Trop long", "date": jour,
        })

        def raison(lang):
            self.persona.invalidate_recordset()
            raisons = [r for _niveau, r in self.persona.with_context(lang=lang)._health_signals()
                       if "Trop long" in r]
            self.assertEqual(len(raisons), 1, raisons)
            return raisons[0]

        anglais, francais = raison("en_CA"), raison("fr_CA")
        jour = fields.Date.to_string(jour)
        self.assertIn(' on %s: "Trop long"' % jour, anglais)
        self.assertNotIn(" : « ", anglais)
        self.assertIn(" le %s : « Trop long »" % jour, francais)

    def _raison_stockee_apres_plainte(self, lang_societe, lang_du_geste):
        self.env.company.partner_id.lang = lang_societe
        self.env["bf.cx.complaint"].with_context(lang=lang_du_geste).create({
            "name": "Facture en double L7", "partner_id": self.partner.id,
            "description": "<p>x</p>",
        })
        self.persona.invalidate_recordset()
        return self.persona.health_reason or ""

    def test_raison_stockee_dans_la_langue_de_la_societe_francaise(self):
        """Plainte saisie en anglais, société en français : la raison stockée est française.

        Le rafraîchissement suivait la langue de qui créait la plainte ; la raison
        (champ non traduit, lu par l'équipe) suit maintenant celle de la société.
        """
        raison = self._raison_stockee_apres_plainte("fr_CA", "en_CA")
        self.assertIn("plainte ouverte depuis le", raison)
        self.assertNotIn("open complaint since", raison)

    def test_raison_stockee_dans_la_langue_de_la_societe_anglaise(self):
        """Plainte saisie en français, société en anglais : la raison stockée est anglaise."""
        raison = self._raison_stockee_apres_plainte("en_CA", "fr_CA")
        self.assertIn("open complaint since", raison)
        self.assertNotIn("plainte ouverte depuis", raison)
