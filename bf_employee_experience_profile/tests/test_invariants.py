"""Les invariants que le module PROMET, et non son comportement observé.

⚠️ Chacun de ces essais nomme un contournement mesuré : une forme par
laquelle une garde du module pouvait être tournée. Ils ne doivent jamais
être « ajustés » à ce que le code fait ; c'est le code qui les rejoint.
"""

from odoo.exceptions import AccessError, ValidationError
from odoo.tests.common import Form, tagged

from .common import ProfileCase


@tagged("post_install", "-at_install")
class TestAdverseResultShape(ProfileCase):
    """Un score appartient au référentiel de sa déclaration, toujours."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user_alice = cls._user("Alice", "alice_adverse")
        cls.user_hr = cls._user("Administration", "hr_adverse",
                                ("base.group_user", "hr.group_hr_user"))
        cls.alice = cls._employee("Alice Adverse", user=cls.user_alice)
        cls.items = {item.code: item for item in cls.behaviour4.item_ids}

    def test_changing_the_framework_cannot_keep_a_foreign_dimension(self):
        """🔴 Mesuré : write(framework_id) sur une déclaration chiffrée garde
        les lignes de l'ANCIEN référentiel. `_check_dimension_framework` vit
        sur la ligne de score et n'est pas rejoué par une écriture du parent,
        et `_check_result_shape` ne regarde que `type_id` du côté chiffré.
        """
        profile = self._scored(self.alice, self.behaviour4, {
            self.items["d"]: 80.0, self.items["s"]: 10.0,
        })
        with self.assertRaises(ValidationError):
            profile.write({"framework_id": self.bigfive.id})

    def test_the_form_switch_drops_the_old_dimensions(self):
        """🔴 Mesuré par le FORMULAIRE livré (« Mon profil ») : l'onchange
        ajoute les dimensions du nouveau référentiel SANS retirer les
        anciennes (`self.score_ids = [(0, 0, ...)]` est additif ; seule la
        branche « type unique » envoie (5, 0, 0)). Deux dimensions DISC + cinq
        Big Five = sept lignes, enregistrées sans un mot.
        """
        profile = self._scored(self.alice, self.behaviour4, {
            self.items["d"]: 80.0, self.items["s"]: 10.0,
        })
        with Form(profile.with_user(self.user_alice),
                  view="bf_employee_experience_profile.view_my_profile_form") as form:
            form.framework_id = self.bigfive
        self.assertEqual(len(profile.score_ids), len(self.bigfive.item_ids))
        self.assertFalse(
            profile.score_ids.filtered(
                lambda s: s.type_id.framework_id != profile.framework_id),
            "une dimension d'un autre référentiel a survécu à la bascule",
        )

    def test_the_dominant_dimension_belongs_to_the_framework(self):
        """🔴 Le résultat FAUX que le gestionnaire lit : après la bascule, la
        déclaration dit « Big Five », « la plus élevée : Dominance (D) » et
        serre le texte « travailler avec » du D. Trois phrases inventées sur
        une personne, affichées comme un fait.
        """
        profile = self._scored(self.alice, self.behaviour4, {
            self.items["d"]: 80.0, self.items["s"]: 10.0,
        })
        with Form(profile.with_user(self.user_alice),
                  view="bf_employee_experience_profile.view_my_profile_form") as form:
            form.framework_id = self.bigfive
        self.assertFalse(
            profile.dominant_type_id
            and profile.dominant_type_id.framework_id != profile.framework_id,
            "« la plus élevée » nomme une dimension d'un autre référentiel : %s"
            % profile.headline,
        )

    def test_a_score_cannot_be_hung_on_a_single_type_declaration(self):
        """La contrainte de forme se contourne par l'enfant : créée depuis
        `bf.ex.profile.score`, la ligne ne déclenche pas le `@api.constrains`
        du parent, alors que la même chose par le parent est refusée.
        """
        profile = self._profile(self.alice)
        with self.assertRaises(ValidationError):
            self.env["bf.ex.profile.score"].create({
                "profile_id": profile.id,
                "type_id": self.type_istj.id,
                "value": 42.0,
            })

    def test_lowering_the_scale_cannot_strand_an_existing_score(self):
        """`_check_scale` ne regarde que le référentiel, `_check_value_in_scale`
        que la ligne : abaisser `scale_max` sous un score déjà saisi passe, et
        la déclaration porte ensuite un 90 sur une échelle de 0 à 50.
        """
        self._scored(self.alice, self.behaviour4, {self.items["d"]: 90.0})
        with self.assertRaises(ValidationError):
            self.behaviour4.write({"scale_max": 50.0})


@tagged("post_install", "-at_install")
class TestAdverseAcknowledgement(ProfileCase):
    """L'accord de la personne : un geste, pas une colonne qu'on coche."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user_alice = cls._user("Alice", "alice_adverse_ack")
        cls.user_bob = cls._user("Bob", "bob_adverse_ack")
        cls.user_hr = cls._user("Administration", "hr_adverse_ack",
                                ("base.group_user", "hr.group_hr_user"))
        cls.alice = cls._employee("Alice Accord", user=cls.user_alice)
        cls.bob = cls._employee("Bob Accord", user=cls.user_bob)

    def test_administration_cannot_forge_the_acceptance_by_a_plain_write(self):
        """🔴 `action_acknowledge` tient, mais le drapeau n'est protégé que
        par `readonly=True`, qui est un attribut d'écran. Un `write` RPC d'une
        ligne pose l'accord de quelqu'un d'autre, et l'aide du champ promet
        exactement le contraire.
        """
        profile = self._profile(self.alice)
        with self.assertRaises(AccessError):
            profile.with_user(self.user_hr).write({"acknowledged": True})
        self.assertFalse(profile.acknowledged)

    def test_copy_does_not_carry_the_acceptance_to_somebody_else(self):
        """🔴 Deuxième porte : `copy({'employee_id': quelqu_un_d_autre})`
        recopie `acknowledged`, `acknowledged_on` et `declared_by_id`. Bob
        apparaît avoir accepté une déclaration qu'il n'a jamais vue.
        """
        profile = self._profile(self.alice)
        profile.with_user(self.user_alice).action_acknowledge()
        clone = profile.with_user(self.user_hr).copy({"employee_id": self.bob.id})
        self.assertFalse(clone.acknowledged)
        self.assertFalse(clone.acknowledged_on)

    def test_a_declaration_entered_by_administration_is_not_sourced_as_self(self):
        """🔴 `source` vaut « self » par défaut : une ligne saisie par les RH
        dit à la personne « Déclaré par la personne », et le filtre « Saisi
        par l'administration » ne la voit pas. `declared_by_id`, lui, dit la
        vérité : les deux champs se contredisent.
        """
        profile = self.env["bf.ex.profile"].with_user(self.user_hr).create({
            "employee_id": self.alice.id,
            "framework_id": self.types16.id,
            "type_id": self.type_istj.id,
        })
        self.assertEqual(profile.source, "hr")


@tagged("post_install", "-at_install")
class TestAdverseDeuxiemeTour(ProfileCase):
    """Ce que le deuxième tour a mesuré : un chemin encore ouvert, et le code
    neuf mis à l'épreuve des gestes légitimes."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user_alice = cls._user("Alice", "alice_adverse2")
        cls.user_hr = cls._user("Administration", "hr_adverse2",
                                ("base.group_user", "hr.group_hr_user"))
        cls.alice = cls._employee("Alice Tour2", user=cls.user_alice)
        cls.hr_fiche = cls._employee("RH Tour2", user=cls.user_hr)
        cls.sans_compte = cls._employee("Sans compte Tour2")

    def _referentiel_maison(self):
        framework = self.env["bf.ex.profile.framework"].create({
            "name": "Maison", "code": "maison_adverse2",
            "result_mode": "scored", "scale_min": 0.0, "scale_max": 100.0,
        })
        items = self.env["bf.ex.profile.type"].create([
            {"framework_id": framework.id, "name": "Un", "code": "un"},
            {"framework_id": framework.id, "name": "Deux", "code": "deux"},
        ])
        return framework, items

    def test_flipping_a_framework_to_single_cannot_strand_its_scores(self):
        """🔴 Le chemin que le premier tour avait manqué : le GRAND-PARENT.

        `_check_result_shape` vit sur la déclaration, `_check_parent_is_scored`
        sur la ligne de score, et aucune des deux n'est rejouée quand c'est le
        RÉFÉRENTIEL qui change de forme. Mesuré : une déclaration chiffrée
        garde ses deux scores, se met à dire « un type par personne » sans
        type, rend « en un coup d'oeil » VIDE, et se laisse encore enregistrer
        (`write({'note': …})` passe). C'est l'état que les deux contraintes
        existent pour interdire, atteint par l'autre bout.
        """
        framework, items = self._referentiel_maison()
        self.env["bf.ex.profile"].create({
            "employee_id": self.alice.id,
            "framework_id": framework.id,
            "score_ids": [
                (0, 0, {"type_id": items[0].id, "value": 70.0}),
                (0, 0, {"type_id": items[1].id, "value": 20.0}),
            ],
        })
        with self.assertRaises(ValidationError):
            framework.write({"result_mode": "single"})

    def test_the_write_guard_leaves_a_legitimate_correction_alone(self):
        """Le code neuf est le plus jeune : voici ce qu'il ne doit PAS casser.

        L'administration corrige une note et une date, la personne écrit la
        sienne, et la fiche employé modifie la déclaration par `(1, id, vals)`.
        Aucun de ces gestes ne touche l'accord ni la trace.
        """
        profile = self._profile(self.alice)
        profile.with_user(self.user_hr).write({"note": "corrigée par les RH"})
        profile.with_user(self.user_hr).write({"assessed_on": self.today})
        profile.with_user(self.user_alice).write({"note": "la mienne"})
        self.env["hr.employee"].with_user(self.user_hr).browse(self.alice.id).write(
            {"ex_profile_ids": [(1, profile.id, {"note": "par la fiche"})]})
        self.assertEqual(profile.note, "par la fiche")
        # Et le geste de la personne, lui, passe toujours.
        profile.with_user(self.user_alice).action_acknowledge()
        self.assertTrue(profile.acknowledged)
        profile.with_user(self.user_alice).action_withdraw_consent()
        self.assertFalse(profile.acknowledged)

    def test_the_source_deduction_covers_every_case(self):
        """La déduction de `source` : quatre cas mesurés, et l'étiquette ne
        doit jamais contredire `declared_by_id`."""
        par_rh = self.env["bf.ex.profile"].with_user(self.user_hr).create({
            "employee_id": self.alice.id, "framework_id": self.types16.id,
            "type_id": self.type_istj.id,
        })
        self.assertEqual(par_rh.source, "hr")
        self.assertEqual(par_rh.declared_by_id, self.user_hr)
        par_elle = self.env["bf.ex.profile"].with_user(self.user_alice).create({
            "employee_id": self.alice.id, "framework_id": self.bigfive.id,
        })
        self.assertEqual(par_elle.source, "self")
        pour_elle_meme = self.env["bf.ex.profile"].with_user(self.user_hr).create({
            "employee_id": self.hr_fiche.id, "framework_id": self.types16.id,
            "type_id": self.type_istj.id,
        })
        self.assertEqual(pour_elle_meme.source, "self",
                         "les RH déclarant pour elles-mêmes déclarent bien")
        sans_compte = self.env["bf.ex.profile"].with_user(self.user_hr).create({
            "employee_id": self.sans_compte.id, "framework_id": self.types16.id,
            "type_id": self.type_istj.id,
        })
        self.assertEqual(sans_compte.source, "hr")
