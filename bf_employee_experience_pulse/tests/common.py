"""Socle commun : une société, des personnes, une vague."""

from odoo.tests.common import TransactionCase


class PulseCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env["res.company"].create({"name": "Pulse Test Inc."})
        cls.env.user.company_ids |= cls.company
        cls.env.user.company_id = cls.company
        cls.dept_a = cls.env["hr.department"].create({
            "name": "Atelier", "company_id": cls.company.id,
        })
        cls.dept_b = cls.env["hr.department"].create({
            "name": "Bureau", "company_id": cls.company.id,
        })
        cls.employees = cls.env["hr.employee"].create([
            {
                "name": "Personne %02d" % i,
                "company_id": cls.company.id,
                "department_id": (cls.dept_a if i % 2 else cls.dept_b).id,
                "work_email": "personne%02d@exemple.test" % i,
            }
            for i in range(1, 9)
        ])
        cls.question_scale = cls.env.ref(
            "bf_employee_experience_pulse.question_workload_1")
        cls.question_text = cls.env.ref(
            "bf_employee_experience_pulse.question_workload_3")
        cls.question_enps = cls.env.ref(
            "bf_employee_experience_pulse.question_enps")

    @classmethod
    def _campaign(cls, **overrides):
        vals = {
            "name": "Vague d'essai",
            "company_id": cls.company.id,
            "question_ids": [(6, 0, [
                cls.question_scale.id,
                cls.question_text.id,
                cls.question_enps.id,
            ])],
        }
        vals.update(overrides)
        return cls.env["bf.ex.pulse.campaign"].create(vals)

    def _repondre(self, invitation, note=8, texte=None, enps=None):
        """Simule une réponse, par le même chemin que le contrôleur."""
        lignes = [{
            "campaign_id": invitation.campaign_id.id,
            "question_id": self.question_scale.id,
            "token": invitation.token,
            "segment_key": invitation.segment_key,
            "value_scale": note,
        }]
        if texte:
            lignes.append({
                "campaign_id": invitation.campaign_id.id,
                "question_id": self.question_text.id,
                "token": invitation.token,
                "segment_key": invitation.segment_key,
                "value_text": texte,
            })
        if enps is not None:
            lignes.append({
                "campaign_id": invitation.campaign_id.id,
                "question_id": self.question_enps.id,
                "token": invitation.token,
                "segment_key": invitation.segment_key,
                "value_scale": enps,
            })
        invitation.write({"used": True})
        return self.env["bf.ex.pulse.staging"].create(lignes)
