from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "bf_training_sign")
class TestTrainingSign(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.aujourdhui = fields.Date.context_today(cls.env["bf.training.record"])
        cls.partenaire = cls.env["res.partner"].create({"name": "Lectrice d'essai"})
        cls.employe = cls.env["hr.employee"].create({
            "name": "Lectrice d'essai",
            "company_id": cls.env.company.id,
            "work_contact_id": cls.partenaire.id,
        })
        cls.type_doc = cls.env["project.document.type"].search([], limit=1) or \
            cls.env["project.document.type"].create({"name": "Politique d'essai"})
        cls.document = cls.env["project.document"].create({
            "name": "Politique de prévention du harcèlement",
            "code": "POL-ESSAI-25660",
            "type_id": cls.type_doc.id,
        })
        cls.v1 = cls.env["project.document.version"].create({
            "document_id": cls.document.id, "version_number": "1.0",
            "state": "released", "release_date": fields.Datetime.now(),
        })
        cls.activite = cls.env["bf.training.activity"]._pour_document(cls.document)

    def _remise(self, version=None, **extra):
        valeurs = {
            "version_id": (version or self.v1).id,
            "partner_id": self.partenaire.id,
            "recipient_type": "partner",
            "distribution_date": fields.Datetime.now(),
        }
        valeurs.update(extra)
        return self.env["project.document.distribution"].create(valeurs)

    # ------------------------------------------------------------------
    def test_l_accuse_ecrit_une_realisation_datee(self):
        remise = self._remise()
        self.assertEqual(remise.training_activity_id, self.activite)
        self.assertFalse(remise.training_record_id)

        remise.action_acknowledge()
        r = remise.training_record_id
        self.assertTrue(r, "L'accusé doit écrire une ligne au registre.")
        self.assertEqual(r.employee_id, self.employe)
        self.assertEqual(r.activity_id, self.activite)
        self.assertEqual(r.date_done, self.aujourdhui)
        self.assertEqual(r.document_version_id, self.v1)
        self.assertEqual(r.state, "confirmed")

    def test_une_signature_exigee_et_absente_n_ecrit_rien(self):
        """🔴 Le refus qui compte : une case cochée ne vaut pas un document signé."""
        remise = self._remise(requires_signature=True)
        remise.action_acknowledge()
        self.assertFalse(remise.training_record_id,
                         "Sans signature, l'accusé ne vaut pas preuve.")

    def test_la_signature_qui_arrive_apres_debloque_l_ecriture(self):
        remise = self._remise(requires_signature=True)
        remise.action_acknowledge()
        self.assertFalse(remise.training_record_id)
        remise.signature_date = fields.Datetime.now()
        self.assertTrue(remise.training_record_id,
                        "Signée après coup, la remise doit enfin compter.")

    def test_un_accuse_n_ecrit_pas_deux_fois(self):
        remise = self._remise()
        remise.action_acknowledge()
        premier = remise.training_record_id
        remise.action_acknowledge()
        self.assertEqual(remise.training_record_id, premier)
        self.assertEqual(self.env["bf.training.record"].search_count(
            [("distribution_id", "=", remise.id)]), 1)

    def test_sans_fiche_employe_rien_n_est_ecrit(self):
        etranger = self.env["res.partner"].create({"name": "Personne hors effectif"})
        remise = self._remise(partner_id=etranger.id)
        remise.action_acknowledge()
        self.assertFalse(remise.training_record_id)
        self.assertFalse(self.env["hr.employee"].sudo().search_count(
            [("work_contact_id", "=", etranger.id)]))

    # ------------------------------------------------------------------
    # Les versions
    # ------------------------------------------------------------------
    def test_une_nouvelle_version_perime_l_accuse_precedent(self):
        remise = self._remise()
        remise.action_acknowledge()
        r = remise.training_record_id
        self.assertFalse(r.is_outdated)

        self.env["project.document.version"].create({
            "document_id": self.document.id, "version_number": "2.0",
            "state": "released", "release_date": fields.Datetime.now(),
        })
        self.document.invalidate_recordset(["latest_version_id"])
        r.invalidate_recordset(["is_outdated"])
        self.assertTrue(r.is_outdated,
                        "La politique a changé : l'accusé d'hier ne vaut plus.")

    def test_une_nouvelle_version_rouvre_l_obligation(self):
        exigence = self.env["bf.training.requirement"].create({
            "name": "Politique lue",
            "requirement_type": "activity",
            "activity_id": self.activite.id,
            "scope": "employees",
            "employee_ids": [(6, 0, [self.employe.id])],
            "trigger": "immediate",
        })
        remise = self._remise()
        remise.action_acknowledge()
        exigence.action_refresh()
        self.assertEqual(exigence.obligation_ids.state, "covered")

        self.env["project.document.version"].create({
            "document_id": self.document.id, "version_number": "2.0",
            "state": "released", "release_date": fields.Datetime.now(),
        })
        self.document.invalidate_recordset(["latest_version_id"])
        exigence.action_refresh()
        self.assertFalse(exigence.obligation_ids.covered_by_id,
                         "La version lue n'est plus celle en vigueur.")

    def test_sans_rouverture_la_nouvelle_version_ne_change_rien(self):
        self.activite.reopen_on_change = False
        remise = self._remise()
        remise.action_acknowledge()
        r = remise.training_record_id
        self.env["project.document.version"].create({
            "document_id": self.document.id, "version_number": "2.0",
            "state": "released", "release_date": fields.Datetime.now(),
        })
        self.document.invalidate_recordset(["latest_version_id"])
        r.invalidate_recordset(["is_outdated"])
        self.assertFalse(r.is_outdated)

    # ------------------------------------------------------------------
    # L'assignation
    # ------------------------------------------------------------------
    def test_assigner_remet_la_version_en_vigueur(self):
        assignation = self.env["bf.training.assignment"].create({
            "partner_id": self.partenaire.id,
            "employee_id": self.employe.id,
            "activity_id": self.activite.id,
        })
        self.assertEqual(assignation.document_id, self.document)
        assignation.action_distribute_document()
        self.assertTrue(assignation.distribution_id)
        self.assertEqual(assignation.distribution_id.version_id, self.v1)
        self.assertEqual(assignation.state, "in_progress")

    def test_assigner_deux_fois_ne_remet_pas_deux_fois(self):
        assignation = self.env["bf.training.assignment"].create({
            "partner_id": self.partenaire.id,
            "activity_id": self.activite.id,
        })
        assignation.action_distribute_document()
        premiere = assignation.distribution_id
        assignation.action_distribute_document()
        self.assertEqual(assignation.distribution_id, premiere)

    def test_l_activite_adossee_rouvre_par_defaut(self):
        """Un document qui change doit être relu : c'est le défaut, pas une option."""
        self.assertTrue(self.activite.reopen_on_change)
