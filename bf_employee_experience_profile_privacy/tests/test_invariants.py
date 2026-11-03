"""Les invariants que le pont vie privée PROMET, et non son comportement.

⚠️ Comme son jumeau du module porteur, ce fichier porte ce que le module
promet : le registre qui ne nomme pas le référentiel, et la garde de droits
de la campagne.
"""

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestAdversePrivacy27279(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.types16 = cls.env.ref(
            "bf_employee_experience_profile.framework_types16")
        cls.istj = cls.env.ref("bf_employee_experience_profile.type_types16_istj")
        cls.calendar = cls.env.ref(
            "bf_employee_experience_profile_privacy.retention_ex_profile")
        cls.employee = cls.env["hr.employee"].create({
            "name": "Alice Registre", "company_id": cls.env.company.id,
        })
        cls.profile = cls.env["bf.ex.profile"].create({
            "employee_id": cls.employee.id,
            "framework_id": cls.types16.id,
            "type_id": cls.istj.id,
        })

    def test_a_campaign_entry_never_names_the_framework(self):
        """🔴 Mesuré : la campagne écrit `res_name` au registre IMMUABLE, et
        `res_name` vient du `display_name` de la déclaration, qui nomme le
        référentiel (« Alice Registre : Seize types (dichotomies
        jungiennes) »). Le texte du cron promet l'inverse, mot pour mot :
        « NI le référentiel, NI le type, NI les scores ». Les allergies y
        échappent parce que `bf.ex.allergy` n'a pas de `display_name`.
        """
        classification = self.env["privacy.document.classification"].create({
            "res_model": "bf.ex.profile",
            "res_id": self.profile.id,
            "pi_category": "other",
            "retention_calendar_id": self.calendar.id,
        })
        campaign = self.env["privacy.destruction.campaign"].create({
            "name": "Campagne adverse",
            "cutoff_date": fields.Date.today(),
            "retention_calendar_id": self.calendar.id,
        })
        campaign.action_scan()
        self.assertTrue(campaign.line_ids, "le balayage n'a rien trouvé")
        campaign.action_approve()
        campaign.action_execute()
        entries = self.env["privacy.destruction.register"].search(
            [("campaign_id", "=", campaign.id)])
        self.assertTrue(entries)
        blob = " ".join(
            (entries.mapped("res_name") or [])
            + (entries.mapped("document_description") or [])
        )
        self.assertNotIn(self.types16.name, blob)
        self.assertNotIn("ISTJ", blob)
        self.assertTrue(classification.exists())

    def test_the_campaign_checks_the_rights_of_whoever_destroys(self):
        """🔴 `record.check_access("unlink")` était posé sur un recordset DEJA
        `sudo()` : il ne levait jamais.

        ⚠️ Deux pièges d'essai, tous deux mesurés, tous deux corrigés ici.
        1. Écrit avec `new()`, l'essai ne prouvait rien : les valeurs d'un
           enregistrement `new()` ne survivent pas à `with_user()`, la méthode
           recevait `res_model=False` et relayait à `super()`.
        2. Écrit avec un usager SANS groupe vie privée, il était vert pour la
           mauvaise raison : l'`AccessError` venait de l'ACL de
           `privacy.destruction.campaign.line`, et la garde n'était jamais
           atteinte. L'essai prend donc un usager qui LIT la ligne
           (`group_privacy_user`) et n'a aucun droit sur le dossier du
           personnel : la seule `AccessError` qui reste est celle de la garde.
        """
        lecteur = self.env["res.users"].create({
            "name": "Lectrice vie privée", "login": "lectrice_adverse_27279",
            "groups_id": [(6, 0, [
                self.env.ref("base.group_user").id,
                self.env.ref("privacy_consent.group_privacy_user").id,
            ])],
        })
        line = self._ligne_de_campagne("Campagne adverse, droits")
        # La preuve que la garde est bien ce qui lève : la ligne est lisible.
        self.assertEqual(line.with_user(lecteur).res_model, "bf.ex.profile")
        self.assertFalse(lecteur.has_group("hr.group_hr_user"))
        with self.assertRaises(AccessError):
            line.with_user(lecteur)._execute_destruction()
        self.assertTrue(self.profile.exists())

    def test_a_privacy_officer_without_hr_rights_still_destroys(self):
        """L'échappatoire du mandat vie privée, qui doit RESTER ouverte.

        Le responsable qui exécute une campagne n'a pas forcément de droit sur
        le dossier du personnel : c'est la campagne qui est l'autorité. Si un
        durcissement futur referme cette porte, une campagne légitime
        s'arrêtera sur sa première déclaration de profil, et cet essai le dira
        avant la production.
        """
        officier = self.env["res.users"].create({
            "name": "Officier", "login": "officier_adverse_27279",
            "email": "officier@example.invalid",
            "groups_id": [(6, 0, [
                self.env.ref("base.group_user").id,
                self.env.ref("privacy_consent.group_privacy_officer").id,
            ])],
        })
        self.assertFalse(officier.has_group("hr.group_hr_user"))
        line = self._ligne_de_campagne("Campagne légitime")
        line.campaign_id.with_user(officier).action_execute()
        self.assertFalse(self.profile.exists())
        entry = self.env["privacy.destruction.register"].search(
            [("campaign_id", "=", line.campaign_id.id)])
        self.assertTrue(entry, "la destruction n'a pas été certifiée")
        self.assertEqual(entry.res_name, self.employee.display_name)
        self.assertNotIn(self.types16.name, entry.document_description or "")

    def _ligne_de_campagne(self, nom):
        """Une VRAIE ligne de campagne, balayée et approuvée."""
        self.env["privacy.document.classification"].create({
            "res_model": "bf.ex.profile",
            "res_id": self.profile.id,
            "pi_category": "other",
            "retention_calendar_id": self.calendar.id,
        })
        campaign = self.env["privacy.destruction.campaign"].create({
            "name": nom,
            "cutoff_date": fields.Date.today(),
            "retention_calendar_id": self.calendar.id,
        })
        campaign.action_scan()
        campaign.action_approve()
        line = campaign.line_ids.filtered(lambda l: l.res_id == self.profile.id)
        self.assertTrue(line, "le balayage n'a pas trouvé la déclaration")
        return line
