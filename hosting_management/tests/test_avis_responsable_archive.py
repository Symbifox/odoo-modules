# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Un avis dont le responsable est archivé PART quand même.

🔴 Relecture adverse du tour 2. ``service.user_id.filtered("active")[:1]`` rend un
recordset VIDE quand le responsable du service est archivé, et le ``continue`` qui
suit faisait alors tomber l'avis en silence : ni courriel, ni journal, ni constat.
C'est strictement pire que l'adressage d'avant, où le courriel partait au moins vers
une boîte lue. La forme avait été retirée de ``document_distribution.py`` au tour 1,
et réintroduite deux fois dans le même lot : les garanties du parc
(``hosting.endpoint``) et le digest des licences (``hosting.license``).

⚠️ Les avis se reconnaissent au numéro de tâche porté par le nom du client, jamais au
libellé du sujet : il est traduit, et la base du banc est une restauration de la
production, donc les deux passes voient aussi de vraies licences et de vrais postes.
"""

from datetime import timedelta

from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestAvisResponsableArchive(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.client = cls.env["res.partner"].create({
            "name": "Client SLA 27668", "is_company": True, "has_sla": True})
        cls.serveur = cls.env["hosting.server"].create({
            "name": "Serveur avis essai", "code": "SRV-AVIS-ESSAI",
            "hostname": "srv-avis.27668.example", "server_type": "production",
            "state": "active"})
        cls.logiciel = cls.env["hosting.software"].create({
            "name": "Logiciel avis essai", "code": "logiciel-avis-essai",
            "software_type": "self_hosted"})
        # Le responsable est vivant à la création, puis archivé : c'est l'ordre réel
        # (un départ, ou l'uid 1 posé par la route de provisionnement).
        cls.parti = cls.env["res.users"].create({
            "name": "Responsable parti 27668",
            "login": "parti.27668@essai.invalid",
            "email": "parti.27668@essai.invalid"})
        cls.service = cls.env["hosting.service"].create({
            "name": "Service avis 27668",
            "software_id": cls.logiciel.id,
            "server_id": cls.serveur.id,
            "partner_id": cls.client.id,
            "state": "active",
            "user_id": cls.parti.id,
        })
        cls.parti.active = False

    def _dernier_courriel(self):
        dernier = self.env["mail.mail"].search([], order="id desc", limit=1)
        return dernier.id or 0

    def _verdict(self, depuis, quoi):
        """Les avis nés de la passe, pour NOTRE client : il en faut un, et vivant."""
        nes = self.env["mail.mail"].search([("id", ">", depuis)])
        notres = nes.filtered(lambda mail: "27668" in (mail.subject or ""))
        self.assertTrue(
            notres,
            "%s : aucun courriel créé, l'avis est tombé en silence" % quoi)
        for mail in notres:
            self.assertTrue(mail.email_to, "%s : un avis sans destinataire" % quoi)
            self.assertNotEqual(
                mail.email_to, self.parti.email,
                "%s : l'avis part encore vers le compte archivé" % quoi)
            destinataire = self.env["res.users"].with_context(
                active_test=False).search([("email", "=", mail.email_to)], limit=1)
            self.assertTrue(
                destinataire, "%s : destinataire inconnu %s" % (quoi, mail.email_to))
            self.assertTrue(
                destinataire.active,
                "%s : l'avis part vers un compte archivé (%s)" % (quoi, mail.email_to))
            self.assertFalse(
                destinataire.share,
                "%s : l'avis part vers un compte portail (%s)" % (quoi, mail.email_to))

    def test_le_digest_des_licences_part_vers_un_compte_vivant(self):
        self.env["hosting.license"].create({
            "name": "Licence avis 27668",
            "owner_partner_id": self.client.id,
            "expiry_date": fields.Date.today() + timedelta(days=30),
            "seats_total": 3,
        })
        depuis = self._dernier_courriel()

        self.env["hosting.license"]._cron_expiry_alerts()

        self._verdict(depuis, "licences")

    def test_le_digest_des_garanties_part_vers_un_compte_vivant(self):
        self.env["hosting.endpoint"].create({
            "name": "Poste avis 27668",
            "partner_id": self.client.id,
            "endpoint_type": "laptop",
            "lifecycle_state": "deployed",
            "assigned_type": "free_text",
            "warranty_end": fields.Date.today() + timedelta(days=30),
        })
        depuis = self._dernier_courriel()

        self.env["hosting.endpoint"]._cron_warranty_alerts()

        self._verdict(depuis, "garanties")
