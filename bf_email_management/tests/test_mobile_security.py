"""Les frontières de sécurité de l'API mobile.

Chaque test ici correspond à une façon concrète de sortir des données de
l'instance. Ce ne sont pas des tests de complétude : ce sont les quatre portes
qu'un client hostile pousserait en premier.
"""
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .common import MobileApiCase


@tagged("post_install", "-at_install")
class TestMobileSecurity(MobileApiCase):

    # ---------------------------------------------------------------- portée
    def test_thread_list_never_leaks_another_users_mail(self):
        """La liste est cadrée sur l'usager de l'appareil, agrégat SQL compris.

        Les requêtes de repli en fils tapent la table directement, donc la
        règle d'enregistrement ne les protège pas : elles répètent
        ``user_id = %s`` elles-mêmes. Si cette répétition disparaît un jour,
        c'est ici que ça se voit.
        """
        result = self.as_owner().get_mobile_threads(filter_name="all", limit=50)
        subjects = [t["subject"] for t in result["threads"]]
        self.assertIn("Rapport mensuel", subjects)
        self.assertNotIn("Courriel d'un autre usager", subjects)

    def test_admin_of_all_mail_still_only_sees_own_on_mobile(self):
        """``group_email_admin`` lit toutes les boîtes dans l'ORM — pas ici.

        Le téléphone est la boîte de son porteur, pas une console
        d'administration : un admin qui ouvre l'app doit voir SA boîte.
        """
        admin_group = self.env.ref("bf_email_management.group_email_admin")
        self.owner.write({"groups_id": [(4, admin_group.id)]})
        result = self.as_owner().get_mobile_threads(filter_name="all", limit=50)
        subjects = [t["subject"] for t in result["threads"]]
        self.assertNotIn("Courriel d'un autre usager", subjects)

    def test_writes_refuse_another_users_row(self):
        """Archiver le courriel d'autrui doit lever, pas réussir en silence."""
        with self.assertRaises(AccessError):
            self.as_owner().mobile_set_handled([self.foreign.id], handled=True)

    def test_reading_another_users_message_is_refused(self):
        with self.assertRaises(UserError):
            self.as_owner().get_mobile_message(self.foreign.id)

    # ------------------------------------------------- pièces jointes sortantes
    def test_arbitrary_attachment_id_cannot_be_mailed_out(self):
        """LA porte à ne pas laisser ouverte.

        Sans le contrôle de réclamation, ``/reply`` accepterait n'importe quel
        ``ir.attachment`` et le posterait : l'export de toute la base en une
        requête, depuis un compte qui n'y a légitimement aucun accès.
        """
        secret = self.env["ir.attachment"].create({
            "name": "contrat-confidentiel.pdf",
            "raw": b"donnees tres confidentielles",
            "res_model": "res.partner",
            "res_id": self.partner.id,
        })
        with self.assertRaises(UserError):
            self.as_owner().browse(self.inbound.id).mobile_reply(
                mode="reply", body="Bonjour.", device=self.device,
                attachment_ids=[secret.id],
            )

    def test_upload_staged_by_another_device_is_refused(self):
        """Un dépôt est lié à l'appareil qui l'a fait, pas seulement à l'usager."""
        other_device = self.env["bf.email.mobile.device"]._issue(
            self.owner.id, name="Autre appareil")
        staged = self.as_owner().mobile_stage_upload(
            other_device, "devis.pdf", b"contenu", "application/pdf")
        with self.assertRaises(UserError):
            self.as_owner().browse(self.inbound.id).mobile_reply(
                mode="reply", body="Bonjour.", device=self.device,
                attachment_ids=[staged["attachment_id"]],
            )

    def test_a_staged_upload_is_single_use(self):
        """Réclamer consomme le dépôt.

        Deux raisons, et la seconde est un vrai dégât : le même identifiant
        pourrait être rattaché à d'autres messages, et surtout la purge des
        dépôts abandonnés supprimerait 24 h plus tard une pièce **déjà
        envoyée**, la retirant du message parti.
        """
        staged = self.as_owner().mobile_stage_upload(
            self.device, "devis.pdf", b"contenu", "application/pdf")
        attachment_id = staged["attachment_id"]

        self.as_owner().browse(self.inbound.id).mobile_reply(
            mode="reply", body="Premier envoi.", device=self.device,
            attachment_ids=[attachment_id],
        )
        # Reparentée hors du marqueur → hors de portée de la purge.
        attachment = self.env["ir.attachment"].sudo().browse(attachment_id)
        self.assertNotEqual(attachment.res_model, "bf.email.mobile.upload")

        with self.assertRaises(UserError):
            self.as_owner().browse(self.inbound.id).mobile_reply(
                mode="reply", body="Deuxième envoi.", device=self.device,
                attachment_ids=[attachment_id],
            )

    def test_gc_never_touches_a_sent_attachment(self):
        """La purge ne doit atteindre que les dépôts jamais réclamés."""
        kept = self.as_owner().mobile_stage_upload(
            self.device, "envoye.pdf", b"x", "application/pdf")
        self.as_owner().browse(self.inbound.id).mobile_reply(
            mode="reply", body="Avec pièce.", device=self.device,
            attachment_ids=[kept["attachment_id"]],
        )
        abandoned = self.as_owner().mobile_stage_upload(
            self.device, "abandonne.pdf", b"y", "application/pdf")
        # Antidaté explicitement : s'appuyer sur `hours=0` ferait dépendre le
        # test de l'ordre, à la milliseconde, entre l'horloge Python et
        # l'horodatage de transaction de PostgreSQL.
        self.env.cr.execute(
            "UPDATE ir_attachment SET create_date = now() - interval '48 hours' "
            "WHERE id = %s", (abandoned["attachment_id"],))
        self.env["ir.attachment"].invalidate_model(["create_date"])

        purged = self.as_owner()._gc_uploads(hours=24)
        self.assertEqual(purged, 1)

        Attachment = self.env["ir.attachment"].sudo()
        self.assertTrue(Attachment.browse(kept["attachment_id"]).exists(),
                        "la pièce envoyée a été supprimée par la purge")
        self.assertFalse(Attachment.browse(abandoned["attachment_id"]).exists(),
                         "le dépôt abandonné aurait dû être purgé")

    # ------------------------------------------------------ modèles autorisés
    def test_routing_target_must_be_allowlisted(self):
        with self.assertRaises(UserError):
            self.as_owner().browse(self.inbound.id).mobile_route(
                "ir.config_parameter", 1)

    def test_record_search_refuses_models_off_the_allowlist(self):
        with self.assertRaises(UserError):
            self.as_owner().mobile_search_records("res.users", "admin")

    def test_spawn_kind_must_be_known(self):
        with self.assertRaises(UserError):
            self.as_owner().browse(self.inbound.id).mobile_spawn("licorne")
