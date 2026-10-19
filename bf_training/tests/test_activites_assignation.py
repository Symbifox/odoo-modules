"""L'assignation porte des activités, comme avant son déménagement.

Quand le modèle vivait dans `bf_security_awareness`, il héritait de
`mail.activity.mixin`. Le déménagement vers ce socle n'a gardé que
`mail.thread` : « Envoyer l'invitation » et la tâche planifiée des retards de la
sensibilisation appellent `activity_schedule` et levaient une AttributeError.
La transaction tombait, et le courriel d'invitation avec elle.

🔴 Le mixin ne suffit pas seul : sans `mail.thread`, créer une activité lève sur
`message_subscribe`. Les deux restent.
"""
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "bf_training")
class TestActivitesAssignation(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.categorie = cls.env["bf.training.category"].create(
            {"name": "Catégorie activités", "code": "QA_ACTIVITES"})
        cls.activite = cls.env["bf.training.activity"].create(
            {"name": "Activité du catalogue", "category_id": cls.categorie.id})
        cls.partenaire = cls.env["res.partner"].create(
            {"name": "Apprenant Activités", "email": "apprenant.activites@exemple.test"})
        cls.assignation = cls.env["bf.training.assignment"].create(
            {"partner_id": cls.partenaire.id, "activity_id": cls.activite.id})

    def test_le_modele_accepte_les_activites(self):
        """🔴 Sans le drapeau, une seule activité fait tomber le tableau des activités."""
        modele = self.env["ir.model"]._get("bf.training.assignment")
        self.assertTrue(modele.is_mail_activity)
        self.assertTrue(modele.is_mail_thread)

    def test_une_activite_se_planifie_sur_l_assignation(self):
        self.assignation.activity_schedule(
            "mail.mail_activity_data_todo", summary="Relancer l'apprenant")
        self.assertEqual(len(self.assignation.activity_ids), 1)
        self.assertEqual(self.assignation.activity_ids.summary, "Relancer l'apprenant")

    def test_le_champ_du_catalogue_garde_son_sens(self):
        """`activity_id` (le catalogue) et `activity_ids` (le mixin) ne se confondent pas."""
        self.assignation.activity_schedule("mail.mail_activity_data_todo")
        self.assertEqual(self.assignation.activity_id, self.activite)
        self.assertEqual(self.assignation._fields["activity_id"].comodel_name,
                         "bf.training.activity")
        self.assertEqual(self.assignation._fields["activity_ids"].comodel_name,
                         "mail.activity")
