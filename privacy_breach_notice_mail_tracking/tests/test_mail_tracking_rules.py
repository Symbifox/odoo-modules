from odoo.exceptions import AccessError
from odoo.tests import tagged

from odoo.addons.privacy_breach_notice.tests.common import BreachNoticeCase


@tagged("post_install", "-at_install")
class TestMailTrackingRules(BreachNoticeCase):
    """mail_tracking rend ses suivis lisibles par tout interne, objet et destinataire compris."""

    def test_les_suivis_d_avis_sont_reserves_au_role(self):
        notice = self._breach()
        notice.action_send()
        message = notice.sudo().mail_id.mail_message_id
        suivi = self.env["mail.tracking.email"].sudo().create({
            "name": "Avis de violation", "mail_message_id": message.id,
            "recipient": notice.sent_to, "state": "sent"})
        self.env["mail.tracking.event"].sudo().create({
            "tracking_email_id": suivi.id, "event_type": "sent"})
        autre = self.env["mail.tracking.email"].sudo().create({"name": "Courriel ordinaire", "recipient": "x@y.invalid"})
        interne = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Interne suivi", "login": "interne-suivi-essai",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id])]})
        # Recherche bornée à nos suivis : sans domaine, mail_tracking lui-même lève une AccessError
        # pour un utilisateur interne dès qu'un autre suivi de la base pointe vers un mail.mail.
        Email = self.env["mail.tracking.email"].with_user(interne)
        nos = [("id", "in", (suivi | autre).ids)]
        self.assertNotIn(suivi, Email.search(nos))
        # mail_tracking refuse déjà la lecture à qui ne lit pas le message. La règle de ce module sert
        # quand l'interne LIT le message (il y est nommé) : le suivi reste caché hors du rôle.
        nomme = notice.with_user(self.manager).message_post(
            body="<p>Pour information</p>", message_type="comment", subtype_xmlid="mail.mt_comment",
            partner_ids=interne.partner_id.ids)
        suivi_nomme = self.env["mail.tracking.email"].sudo().create({
            "name": "Note nommée", "mail_message_id": nomme.id, "recipient": "interne@essai.invalid"})
        self.assertTrue(nomme.with_user(interne).read(["body"]), "l'interne nommé lit le message")
        with self.assertRaises(AccessError):
            Email.browse(suivi_nomme.id).read(["name", "recipient"])
        self.assertIn(autre, Email.search(nos), "les autres suivis, orphelins compris, restent visibles")
        self.assertFalse(self.env["mail.tracking.event"].with_user(interne).search(
            [("tracking_email_id", "=", suivi.id)]))
        self.assertIn(suivi, self.env["mail.tracking.email"].with_user(self.reader).search(nos))
        # La branche « autre modèle » : un suivi d'un message lisible reste visible à l'interne.
        message_partenaire = self.org.sudo().message_post(body="<p>Bonjour</p>", message_type="comment")
        ordinaire = self.env["mail.tracking.email"].sudo().create({
            "name": "Courriel au client", "mail_message_id": message_partenaire.id, "recipient": "c@y.invalid"})
        self.assertIn(ordinaire, Email.search([("id", "=", ordinaire.id)]))

    def test_un_lecteur_d_une_autre_societe_ne_voit_pas_les_evenements(self):
        notice = self._breach()
        notice.action_send()
        message = notice.sudo().mail_id.mail_message_id
        self.assertEqual(message.record_company_id, notice.company_id)
        suivi = self.env["mail.tracking.email"].sudo().create({
            "name": "Avis", "mail_message_id": message.id, "recipient": notice.sent_to})
        evenement = self.env["mail.tracking.event"].sudo().create({"tracking_email_id": suivi.id, "event_type": "sent"})
        autre = self.env["res.company"].create({"name": "Autre société suivi"})
        lecteur = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Lecteur B", "login": "lecteur-b-suivi-essai", "company_id": autre.id,
            "company_ids": [(6, 0, autre.ids)],
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id,
                                  self.env.ref("privacy_consent.group_privacy_user").id])]})
        self.assertFalse(self.env["mail.tracking.event"].with_user(lecteur).search([("id", "=", evenement.id)]))
