"""Ce que le registre des traitements dit du quart, et ce qu'il refuse de dire.

Le module ne porte qu'un enregistrement de données. C'est justement pour ça
qu'il a besoin de tests : un module dont tout le contenu est une donnée n'a
aucun code qui échoue bruyamment. Une faute de frappe dans le `code`, un
`requires_consent` qui bascule, l'enregistrement qui ne se charge pas chez un
client — rien de tout cela ne se voit, et le registre annonce alors autre chose
que ce que la suite fait.

🔴 Ce module était le seul des quatre modules
d'exploitation sans aucun test, et le seul de la famille vie privée de la
maison dans ce cas — `bf_property_privacy`, `bf_employee_experience_privacy` et
`bf_meeting_privacy` en ont chacun un.

Trois idées :

1. **La finalité existe, et elle est celle-là.** Le registre nomme le quart.
2. **Elle ne demande PAS de consentement**, et c'est le cœur de la décision :
   demander à un concierge de consentir à ce qu'on sache qui a fait la ronde
   laisserait croire qu'il peut refuser.
3. **Le texte en langage clair est ce qu'on a MONTRÉ**, donc il ne se réécrit
   pas à la mise à jour du module.
"""
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestOperationsPrivacy(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.purpose = cls.env.ref(
            "bf_property_operations_privacy.purpose_property_shift",
            raise_if_not_found=False,
        )

    # ── 1. La finalité est au registre ──

    def test_the_shift_has_its_purpose_in_the_register(self):
        self.assertTrue(
            self.purpose,
            "Le quart nomme des salariés et n'est déclaré nulle part.",
        )
        self.assertEqual(self.purpose.code, "property_shift")

    def test_the_purpose_is_distinct_from_the_three_of_the_suite(self):
        """⚠️ Le contrôle qui prouve qu'on n'a pas rangé le quart sous une
        finalité existante. Les trois de `bf_property_privacy` reposent sur
        l'art. 1070 al. 1, sur un consentement exprès et sur l'information de
        tiers ; celle-ci repose sur la relation d'emploi, qui n'est aucune des
        trois."""
        others = [
            "bf_property_privacy.purpose_property_register",
            "bf_property_privacy.purpose_property_sms",
            "bf_property_privacy.purpose_property_access_log",
        ]
        for xmlid in others:
            other = self.env.ref(xmlid, raise_if_not_found=False)
            if other:
                self.assertNotEqual(self.purpose, other)
                self.assertNotEqual(self.purpose.code, other.code)

    # ── 2. 🔴 Aucun consentement, et c'est la décision ──

    def test_no_consent_is_asked_of_an_employee(self):
        """🔴 Un consentement qu'on ne peut pas retirer n'est pas un
        consentement, c'est un formulaire. Refuser de rendre compte de son
        travail n'est pas une option offerte à un salarié : lui présenter la
        question comme un choix serait faux."""
        self.assertFalse(self.purpose.requires_consent)
        self.assertFalse(self.purpose.requires_express_opt_in)

    def test_no_consent_notice_hangs_off_this_purpose(self):
        """⚠️ Le contrôle qui tient la décision côté données, pas seulement
        côté drapeau : un avis attaché à cette finalité rouvrirait par la
        porte de derrière la question qu'on vient de fermer."""
        self.assertFalse(self.purpose.notice_ids)

    # ── 3. Le texte montré aux personnes ──

    def test_the_plain_language_summary_says_what_is_collected(self):
        """La Loi 25 laisse ici l'obligation d'informer de la finalité. Le
        résumé doit donc nommer ce qui est enregistré et sur quoi cela
        repose."""
        summary = self.purpose.plain_language_summary or ""
        for expected in ("quart", "relation d'emploi", "consentement"):
            self.assertIn(expected, summary)

    def test_the_summary_denies_what_the_shift_is_not(self):
        """⚠️ Le quart n'est pas un dossier disciplinaire, et le dire au
        registre est ce qui borne le traitement. Odoo a `hr_attendance` pour
        les heures ; le quart s'arrête au travail."""
        summary = self.purpose.plain_language_summary or ""
        for denied in ("rendement", "présence", "heures travaillées"):
            self.assertIn(denied, summary)

    def test_the_record_is_noupdate_so_the_shown_text_is_not_rewritten(self):
        """🔴 Le texte en langage clair est ce qu'on a MONTRÉ aux personnes.
        Une mise à jour du module qui le réécrirait changerait après coup ce
        qu'elles ont lu, sans que personne le sache."""
        data = self.env["ir.model.data"].search(
            [
                ("module", "=", "bf_property_operations_privacy"),
                ("name", "=", "purpose_property_shift"),
            ]
        )
        self.assertTrue(data)
        self.assertTrue(data.noupdate)

    # ── Le pont s'installe seul, et seulement là où les deux côtés sont ──

    def test_the_bridge_installs_itself_when_both_sides_are_there(self):
        """⚠️ Déclarer la finalité dans `bf_property_privacy` l'aurait inscrite
        au registre même là où aucun quart n'existe. Un registre qui annonce un
        traitement qui n'a pas lieu est aussi faux qu'un registre qui en tait
        un."""
        module = self.env["ir.module.module"].search(
            [("name", "=", "bf_property_operations_privacy")]
        )
        self.assertTrue(module.auto_install)
        self.assertEqual(
            sorted(module.dependencies_id.mapped("name")),
            ["bf_property_operations", "bf_property_privacy"],
        )
