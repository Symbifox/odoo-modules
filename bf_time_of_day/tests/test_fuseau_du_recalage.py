from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestFuseauDuRecalage(TransactionCase):
    """Le recalage sur la plage garde le jour du fuseau de l'écriture.

    Le fuseau du contexte passe avant celui de l'usager, comme dans
    fields.Datetime.context_timestamp : une écriture faite au nom d'une autre
    zone (la fédération, un usager technique sans fuseau) dit ainsi de quel jour
    elle parle.
    """

    def setUp(self):
        super().setUp()
        self.eod = self.env.ref("bf_time_of_day.tod_eod")  # 16 h
        self.task = self.env["project.task"].create({
            "name": "Échéance recalée",
            "project_id": self.env["project.project"].create({"name": "Recalage"}).id,
            "time_of_day_id": self.eod.id,
        })

    def _ecrire(self, task, valeur):
        task.write({"date_deadline": valeur})
        self.env.cr.flush()
        return self.task.date_deadline

    def test_le_fuseau_du_contexte_garde_son_jour(self):
        # 8 oct. midi à Auckland = 7 oct. 23 h UTC ; la plage le porte au 8 oct. 16 h, à Auckland.
        self.env.user.tz = False
        got = self._ecrire(self.task.with_context(tz="Pacific/Auckland"), "2026-10-07 23:00:00")
        self.assertEqual(got, fields.Datetime.to_datetime("2026-10-08 03:00:00"))

    def test_sans_fuseau_de_contexte_celui_de_l_usager(self):
        self.env.user.tz = "America/Toronto"
        got = self._ecrire(self.task.with_context(tz=False), "2026-10-08 16:00:00")
        self.assertEqual(got, fields.Datetime.to_datetime("2026-10-08 20:00:00"))

    def test_fuseau_de_contexte_inconnu(self):
        self.env.user.tz = "America/Toronto"
        got = self._ecrire(self.task.with_context(tz="Nulle/Part"), "2026-10-08 16:00:00")
        self.assertEqual(got, fields.Datetime.to_datetime("2026-10-08 20:00:00"))
