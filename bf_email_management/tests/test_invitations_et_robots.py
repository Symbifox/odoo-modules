"""Le drapeau d'invitation, et le motif des robots élargi.

Deux défauts mesurés sur une base réelle le 2026-09-13 :

1. La recette « calendar » du catalogue cherchait `Content-Type: text/calendar`
   dans `raw_headers`, qui ne garde que les en-têtes de PREMIER NIVEAU. Une
   invitation est un multipart dont une PARTIE porte ce type : **0 ligne sur
   8 432** satisfaisait la condition. La recette ne pouvait jamais se
   déclencher, ce qui est le miroir de la garde « une règle sans condition ne
   se déclenche jamais ».
2. Le motif des expéditeurs automatiques ne reconnaissait qu'**une** des 1 357
   lignes encore en boîte. `donotreply@` sans traits d'union (234 lignes à lui
   seul), `donotreply-nepasrepondre@` d'un transporteur (40) et
   `notifications@` d'une autre instance (13) lui échappaient tous.
"""
import base64
from email.message import EmailMessage

from odoo.tests import tagged

from .common import MobileApiCase


def _avec_ical(subject, sender):
    """Un multipart dont UNE PARTIE est text/calendar, comme les vrais."""
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = "owner@test.invalid"
    msg["Message-ID"] = "<ical-%s@test.invalid>" % abs(hash(subject))
    msg.set_content("Voici l'invitation.")
    msg.add_attachment(
        b"BEGIN:VCALENDAR\nVERSION:2.0\nEND:VCALENDAR\n",
        maintype="text", subtype="calendar", filename="invite.ics")
    return base64.b64encode(msg.as_bytes()).decode(), msg


@tagged("post_install", "-at_install")
class TestInvitationsEtRobots(MobileApiCase):

    def _ligne(self, subject, sender="client@acme.test", uid="900",
               calendar=False, headers=None):
        vals = {
            "subject": subject,
            "email_from": sender,
            "email_to": "owner@test.invalid",
            "direction": "in", "status": "new", "source": "imap",
            "account_id": self.account.id,
            "user_id": self.owner.id,
            "imap_folder": "INBOX", "imap_uid": uid,
            "message_id_header": "<inv-%s@test.invalid>" % uid,
            "date": "2026-08-20 14:00:00",
            "has_calendar_part": calendar,
        }
        if headers:
            vals["raw_headers"] = headers
        return self.env["bf.email"].with_user(self.owner).create(vals)

    # -- le drapeau -----------------------------------------------------
    def test_une_partie_ical_arme_le_drapeau(self):
        rec = self._ligne("Réunion de projet", uid="901", calendar=True)
        self.assertTrue(rec.is_invitation)

    def test_l_objet_sert_de_repli(self):
        """Les lignes déjà en base n'ont pas marché leurs parties MIME."""
        for objet in ("Invitation: Rencontre statutaire",
                      "Accepted: Rencontre statutaire",
                      "Declined: Rencontre statutaire",
                      "Re: Invitation: Rencontre",
                      "Invitation annulée : Rencontre"):
            rec = self._ligne(objet, uid=str(abs(hash(objet)) % 9000 + 1000))
            self.assertTrue(rec.is_invitation, objet)

    def test_un_courriel_ordinaire_n_est_pas_une_invitation(self):
        rec = self._ligne("Suivi du dossier", uid="902")
        self.assertFalse(rec.is_invitation)

    def test_le_multipart_ne_dit_rien_dans_raw_headers(self):
        """🔴 Le défaut exact : l'en-tête de premier niveau dit `multipart`.

        Sans `has_calendar_part`, une vraie invitation reste invisible à la
        règle. C'est la mesure qui l'a montré, pas une intuition : 8 432 lignes
        portent un Content-Type, 7 383 disent multipart, zéro dit text/calendar.
        """
        _b64, msg = _avec_ical("Réunion", "client@acme.test")
        entetes = "\n".join("%s: %s" % (k, v) for k, v in msg.items())
        self.assertIn("multipart", entetes.lower())
        self.assertNotIn("text/calendar", entetes.lower())
        sans_drapeau = self._ligne("Réunion sans drapeau", uid="903",
                                   headers=entetes)
        self.assertFalse(
            sans_drapeau.is_invitation,
            "c'est exactement pourquoi le drapeau se pose à la collecte")

    def test_la_recette_calendar_se_declenche_enfin(self):
        from ..models.bf_email_rule import RECIPES_BY_KEY
        recette = RECIPES_BY_KEY["calendar"]
        regle = self.env["bf.email.rule"].create(
            self.env["bf.email.rule"]._recipe_to_vals(
                recette, user=self.owner, company=self.env.company))
        rec = self._ligne("Réunion de projet", uid="904", calendar=True)
        self.assertTrue(regle._match(rec))

    # -- le motif des robots --------------------------------------------
    def test_les_formes_que_le_motif_ratait(self):
        Email = self.env["bf.email"]
        # Les FORMES relevées sur un corpus réel, avec des domaines d'exemple
        # (RFC 2606) : ce qui compte ici est la partie locale, pas qui l'écrit.
        ratees = [
            "donotreply@banque.test",
            "donotreply-nepasrepondre@communications.transporteur.test",
            "notifications@autre-instance.test",
            "nepasrepondre@securite.telecom.test",
            "alerts@alerts.supervision.test",
            "notify@payments.paiements.test",
            "auto-confirm@marchand.test",
            "no.reply@courtier.test",
        ]
        for adresse in ratees:
            self.assertTrue(
                Email._NOTIFICATION_PATTERNS.search(adresse), adresse)

    def test_ce_qui_doit_rester_humain(self):
        """⚠️ Un motif trop large classerait du courrier de client en machine."""
        Email = self.env["bf.email"]
        humains = [
            "auto@concessionnaire.test",
            "notaire@etude.test",
            "alertequebec@client.test",
            "prenom@client.test",
            "reply@client.test",
            "autonomie@organisme.test",
        ]
        for adresse in humains:
            self.assertFalse(
                Email._NOTIFICATION_PATTERNS.search(adresse), adresse)

    def test_un_robot_est_classe_en_notification(self):
        rec = self._ligne("Votre relevé", sender="donotreply@banque.test",
                          uid="905")
        self.assertEqual(rec.category, "notification")
