"""La langue d'une phrase suit la personne qui la lit, jamais celle qui l'a calculée.

🔴 Trouvé en lisant l'interface en anglais. Les phrases qui
expliquent une assemblée (règle de quorum, plafond de voix, contrôle de l'urne,
motif du résultat, défaut de mode) étaient des champs calculés STOCKÉS : elles se
rangeaient en base dans la langue de qui avait déclenché le calcul. Une
gestionnaire francophone lisait l'anglais dès qu'un collègue anglophone avait
enregistré en dernier, et l'inverse. Le catalogue `en_CA.po` n'y changeait rien :
les chaînes y étaient.

Chaque essai calcule dans une langue et relit dans l'autre, dans les deux sens.
⚠️ C'est le croisement qui mord. Relire en français une phrase calculée en
français passe même avec le défaut ; relire en anglais une phrase calculée en
anglais aussi.
"""
from datetime import datetime, timedelta

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
        """Calcule sous la langue de l'écrivain, relit sous celle du lecteur.

        `build(env)` monte le cas avec l'environnement de l'écrivain et rend
        l'enregistrement à relire ; `expected` donne, champ par champ, le
        fragment attendu dans chaque langue. Le cache est vidé avant chaque
        lecture : un champ stocké se relit alors en base, là où le défaut vit.
        """
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

    def _syndicat(self, env, quote_parts):
        syndicat = env["bf.property.organisation"].create(
            {"name": "Syndicat bilingue", "fraction_base": sum(quote_parts)}
        )
        building = env["bf.property.building"].create(
            {"name": "Immeuble bilingue", "organisation_id": syndicat.id}
        )
        for index, quote_part in enumerate(quote_parts, start=1):
            unit = env["bf.property.unit"].create(
                {"name": "B-%d" % index, "building_id": building.id,
                 "quote_part": quote_part}
            )
            owner = env["res.partner"].create(
                {"name": "Copropriétaire %d" % index,
                 "email": "b%d@example.invalid" % index}
            )
            env["bf.property.ownership"].create(
                {"unit_id": unit.id, "partner_id": owner.id}
            )
        return syndicat

    def _assembly(self, env, syndicat, **kw):
        vals = {
            "name": "AG bilingue",
            "organisation_id": syndicat.id,
            "date": datetime.now() + timedelta(days=20),
            "convocation_date": datetime.now().date(),
        }
        vals.update(kw)
        assembly = env["bf.property.assembly"].create(vals)
        assembly.action_load_attendance()
        return assembly

    # ── L'assemblée ──

    def test_the_assembly_rules_follow_the_reader(self):
        def build(env):
            return self._assembly(
                env, self._syndicat(env, [600.0, 400.0]),
                participation_mode="remote",
            )

        self._crossed(build, {
            "quorum_rule": {
                "fr_CA": "copropriétaires détenant la majorité des voix",
                "en_CA": "co-owners holding a majority of the votes",
            },
            "participation_warning": {
                "fr_CA": "Les moyens technologiques ne sont pas renseignés",
                "en_CA": "The means of communication are not entered",
            },
        })

    def test_the_mode_filter_still_finds_the_faulty_assemblies(self):
        """Le filtre « Défaut de mode » cherchait sur le champ stocké.

        Rendue à la lecture, la phrase ne se cherche plus en base : le filtre
        doit trouver la même chose par les faits qui la font naître.
        """
        syndicat = self._syndicat(self.env, [600.0, 400.0])
        faulty = self._assembly(self.env, syndicat, participation_mode="remote")
        attested = self._assembly(
            self.env, syndicat, participation_mode="hybrid",
            remote_means="Visioconférence", remote_immediate_communication=True,
        )
        in_person = self._assembly(self.env, syndicat)
        Assembly = self.env["bf.property.assembly"]
        mine = [("organisation_id", "=", syndicat.id)]
        self.assertEqual(
            Assembly.search(mine + [("participation_warning", "!=", False)]),
            faulty,
        )
        self.assertEqual(
            Assembly.search(mine + [("participation_warning", "=", False)]),
            attested | in_person,
        )

    def test_the_vote_cap_follows_the_reader(self):
        """Art. 1091 : deux fractions, le porteur de 600 voix est plafonné."""
        def build(env):
            assembly = self._assembly(env, self._syndicat(env, [600.0, 400.0]))
            assembly.attendance_ids.write({"status": "present"})
            return assembly.attendance_ids.filtered(
                lambda line: line.base_votes == 600.0
            )

        self._crossed(build, {
            "cap_rule": {
                "fr_CA": "Art. 1091 C.c.Q. : moins de cinq fractions",
                "en_CA": "Art. 1091 CCQ: fewer than five fractions",
            },
        })

    def test_the_council_warning_follows_the_reader(self):
        def build(env):
            return env["bf.property.council.meeting"].create({
                "name": "Réunion bilingue",
                "organisation_id": self._syndicat(env, [1000.0]).id,
                "date": datetime.now() - timedelta(days=1),
                "participation_mode": "remote",
            })

        self._crossed(build, {
            "participation_warning": {
                "fr_CA": "Les moyens technologiques ne sont pas indiqués",
                "en_CA": "The means of communication are not stated",
            },
        })

    # ── La résolution ──

    def test_the_resolution_texts_follow_the_reader(self):
        """Motif du résultat, contrôle de l'urne et règle de majorité.

        ⚠️ La règle de majorité n'était même pas stockée : c'était une
        constante de module sans `_()`, donc du français dans toutes les
        langues. Même symptôme, autre cause.
        """
        def build(env):
            assembly = self._assembly(env, self._syndicat(env, [600.0, 400.0]))
            return env["bf.property.resolution"].create({
                "name": "Résolution bilingue",
                "assembly_id": assembly.id,
                "ballot_mode": "secret",
                "majority_type": "art_1097",
            })

        self._crossed(build, {
            "result_detail": {
                "fr_CA": "Aucun scrutin consigné.",
                "en_CA": "No ballot recorded.",
            },
            "ballot_box_detail": {
                "fr_CA": "Le scrutin n'a pas été ouvert.",
                "en_CA": "The ballot was not opened.",
            },
            "majority_label": {
                "fr_CA": "trois quarts des voix des copropriétaires présents",
                "en_CA": "three-quarters of the votes of the co-owners present",
            },
        })
