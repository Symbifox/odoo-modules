"""La langue d'une échéance réglementaire suit la personne qui la lit.

🔴 C'est ici que le défaut s'est mesuré : la
règle de révision du carnet, recalculée par une administratrice `en_CA`, se
relisait en anglais par une lectrice `fr_CA`. Même chose pour le régime
transitoire de la Loi 16 et pour ce qu'il manque à une attestation.

Chaque essai calcule dans une langue et relit dans l'autre, dans les deux sens.
Voir `bf_property_governance/tests/test_reader_language.py` pour le patron.
"""
from datetime import date

from odoo import fields
from odoo.tests.common import TransactionCase, tagged

CROSSINGS = (("en_CA", "fr_CA"), ("fr_CA", "en_CA"))


@tagged("post_install", "-at_install")
class TestReaderLanguage(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for code in ("fr_CA", "en_CA"):
            cls.env["res.lang"]._activate_lang(code)

    def _crossed(self, build, expected):
        """Calcule sous la langue de l'écrivain, relit sous celle du lecteur."""
        for writer, reader in CROSSINGS:
            env = self.env(context=dict(self.env.context, lang=writer))
            record = build(env)
            env.flush_all()
            for fname, fragments in expected.items():
                read = record.with_context(lang=reader)
                read.invalidate_recordset([fname])
                self.assertIn(
                    fragments[reader],
                    read[fname] or "",
                    "%s.%s calculé en %s, relu en %s"
                    % (record._name, fname, writer, reader),
                )

    def _syndicat(self, env, **kw):
        vals = {"name": "Syndicat bilingue", "fraction_base": 1000}
        vals.update(kw)
        return env["bf.property.organisation"].create(vals)

    def test_the_transitional_rule_follows_the_reader(self):
        self._crossed(lambda env: self._syndicat(env), {
            "loi16_rule": {
                "fr_CA": "La date de l'assemblée de l'art. 1104 C.c.Q. n'est pas renseignée",
                "en_CA": "The date of the meeting under art. 1104 CCQ is not entered",
            },
        })

    def test_the_revision_rule_follows_the_reader(self):
        """Le cas mesuré : `revision_rule` du carnet."""
        def build(env):
            return env["bf.property.maintenance.log"].create({
                "name": "Carnet bilingue",
                "organisation_id": self._syndicat(env).id,
            })

        self._crossed(build, {
            "revision_rule": {
                "fr_CA": "Art. 5 al. 1 du règlement : révision minimalement",
                "en_CA": "S. 5 para. 1 of the regulation: review at least",
            },
        })

    def test_the_missing_items_follow_the_reader(self):
        def build(env):
            syndicat = self._syndicat(env, promoter_handover_date=date(2015, 6, 1))
            building = env["bf.property.building"].create(
                {"name": "Immeuble bilingue", "organisation_id": syndicat.id}
            )
            unit = env["bf.property.unit"].create(
                {"name": "R-1", "building_id": building.id, "quote_part": 1000.0}
            )
            return env["bf.property.attestation"].create({
                "organisation_id": syndicat.id,
                "unit_id": unit.id,
                "requester_partner_id": env["res.partner"].create(
                    {"name": "Vendeur bilingue"}
                ).id,
                "request_date": fields.Date.today(),
            })

        self._crossed(build, {
            "missing_items": {
                "fr_CA": "7. la plus haute franchise (par. 7°)",
                "en_CA": "7. the highest deductible (subpara. 7)",
            },
        })
