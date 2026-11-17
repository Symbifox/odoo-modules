# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Un travail planifié ne s'adresse jamais à un compte archivé.

Les tâches planifiées tournent sous ``__system__`` (uid 1), archivé dans toute base :
le repli ``service.user_id or self.env.user`` posait l'activité d'expiration sur un
compte que personne n'ouvre.
"""

from datetime import timedelta

from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestAssigneVivant(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.client = cls.env["res.partner"].create({
            "name": "Client Expiration 27668", "is_company": True})
        cls.serveur = cls.env["hosting.server"].create({
            "name": "Serveur essai", "code": "SRV-ESSAI", "hostname": "srv.essai.example",
            "server_type": "production", "state": "active"})
        cls.logiciel = cls.env["hosting.software"].create({
            "name": "Logiciel essai", "code": "logiciel-essai",
            "software_type": "self_hosted"})
        cls.robot = cls.env["res.users"].create({
            "name": "Robot hébergement 27668", "login": "robot.hosting.27668@essai.invalid"})

    def _service(self, **extra):
        valeurs = {
            "name": "Service 27668",
            "software_id": self.logiciel.id,
            "server_id": self.serveur.id,
            "partner_id": self.client.id,
            "state": "active",
            "date_expiration": fields.Date.today() + timedelta(days=30),
        }
        valeurs.update(extra)
        return self.env["hosting.service"].create(valeurs)

    def _activites(self, service):
        return self.env["mail.activity"].search([
            ("res_model", "=", "hosting.service"), ("res_id", "=", service.id)])

    def test_un_responsable_archive_ne_recoit_pas_l_activite_d_expiration(self):
        service = self._service(user_id=self.robot.id)
        self.robot.active = False

        self.env["hosting.service"]._cron_check_expirations()

        activites = self._activites(service)
        self.assertTrue(activites, "l'activité d'expiration doit exister")
        self.assertTrue(all(activites.mapped("user_id.active")),
                        "aucune activité ne doit viser un compte archivé")

    def test_sans_responsable_l_activite_va_a_un_compte_vivant(self):
        service = self._service()

        self.env["hosting.service"]._cron_check_expirations()

        activites = self._activites(service)
        self.assertTrue(activites)
        self.assertTrue(all(activites.mapped("user_id.active")))

    def test_la_route_de_provisionnement_ne_pose_pas_un_responsable_archive(self):
        """🔴 Fermer le GÉNÉRATEUR, pas seulement l'avis.

        ``controllers/provisioning_api.py`` tourne en ``request.env(user=1)`` et crée
        le service SANS ``user_id`` : le défaut du champ décidait donc seul, et il
        rendait ``self.env.user``, soit l'uid 1, que Odoo garde archivé dans toute
        base. C'est la cause des 4 services à l'uid 1 du ménage du 2026-11-13.

        ⚠️ Et le lot enlevait le SIGNAL sans fermer la cause : depuis que le repli des
        avis choisit un compte vivant, un service provisionné gardait un responsable
        archivé pour toujours sans qu'aucun constat le dise.
        """
        env = self.env(user=1)
        self.assertFalse(
            env.user.active,
            "l'uid 1 doit bien être le compte archivé sous lequel tourne la route")

        service = env["hosting.service"].create({
            "name": "Service provisionné 27668",
            "software_id": self.logiciel.id,
            "server_id": self.serveur.id,
            "partner_id": self.client.id,
            "state": "active",
        })

        self.assertTrue(
            service.user_id,
            "un service créé sans responsable doit en recevoir un, pas rester nu")
        self.assertNotEqual(
            service.user_id.id, 1,
            "le responsable est l'uid 1, le compte que personne n'ouvre")
        self.assertTrue(
            service.user_id.active, "le responsable obtenu est archivé")
        self.assertFalse(
            service.user_id.share, "le responsable obtenu est un compte portail")
