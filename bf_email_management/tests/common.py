"""Socle commun des tests de l'API mobile.

Construit une boîte réaliste plutôt qu'un minimum syndical : un fil de deux
messages (pour éprouver le repli sur ``thread_root_id``), un orphelin avec
pièce jointe et image distante (pour le corps, le blocage et le téléchargement),
et un message d'un AUTRE usager — celui-là ne devrait jamais apparaître, et
c'est précisément ce qu'on vérifie.
"""
import base64
from email.message import EmailMessage

from odoo.tests import TransactionCase


def build_rfc822(subject, sender, to, body, attachment=None, cc=None):
    """Un message RFC 2822 complet : texte, HTML, pixel distant, pièce jointe."""
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    msg["Message-ID"] = "<%s@test.invalid>" % abs(hash((subject, sender)))
    msg.set_content(body)
    msg.add_alternative(
        "<html><body><p>%s</p>"
        "<img src='https://pisteur.test/pixel.gif'/>"
        "<img src='cid:inline'/></body></html>" % body,
        subtype="html",
    )
    if attachment:
        msg.add_attachment(b"colonne A;colonne B\n1;2\n", maintype="text",
                           subtype="csv", filename=attachment)
    return base64.b64encode(msg.as_bytes()).decode()


class MobileApiCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Users = cls.env["res.users"].with_context(no_reset_password=True)

        cls.owner = Users.create({
            "name": "Propriétaire Boîte",
            "login": "mobile.owner@test.invalid",
            "email": "owner@test.invalid",
            "tz": "America/Montreal",
            "signature": "<p>-- <br/>Propriétaire</p>",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
        })
        cls.stranger = Users.create({
            "name": "Autre Usager",
            "login": "mobile.stranger@test.invalid",
            "email": "stranger@test.invalid",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
        })

        cls.account = cls.env["bf.email.account"].create({
            "name": "Boîte de test",
            "user_id": cls.owner.id,
            "host": "imap.test.invalid",
            "port": 993,
            "login": "owner@test.invalid",
            "password": "x",
            "state": "connected",
        })

        cls.partner = cls.env["res.partner"].create({
            "name": "Client Acme",
            "email": "client@acme.test",
        })

        BfEmail = cls.env["bf.email"].with_user(cls.owner)

        # Fil de deux messages partageant la même racine RFC 2822.
        cls.inbound = BfEmail.create(cls._vals(
            subject="Question sur la facture", sender="client@acme.test",
            direction="in", status="new", root="<racine-1@test.invalid>",
            body="Pouvez-vous confirmer le montant ?", uid="101",
        ))
        cls.outbound = BfEmail.create(cls._vals(
            subject="Re: Question sur la facture", sender="owner@test.invalid",
            direction="out", status="read", root="<racine-1@test.invalid>",
            body="Oui, 1 250 $.", uid="102",
        ))
        # Orphelin : pas de racine de fil, une pièce jointe.
        cls.with_attachment = BfEmail.create(cls._vals(
            subject="Rapport mensuel", sender="rapports@fournisseur.test",
            direction="in", status="new", root=False,
            body="Ci-joint le rapport.", uid="103", attachment="rapport.csv",
        ))
        # Appartient à quelqu'un d'autre : ne doit JAMAIS ressortir.
        cls.foreign = cls.env["bf.email"].with_user(cls.stranger).create({
            "subject": "Courriel d'un autre usager",
            "email_from": "secret@ailleurs.test",
            "direction": "in", "status": "new", "source": "imap",
            "user_id": cls.stranger.id,
            "imap_in_inbox": True,
            "message_id_header": "<etranger@test.invalid>",
            "date": "2026-08-10 12:00:00",
        })

        cls.device = cls.env["bf.email.mobile.device"]._issue(
            cls.owner.id, name="Appareil de test")

    @classmethod
    def _vals(cls, subject, sender, direction, status, root, body, uid,
              attachment=None):
        return {
            "subject": subject,
            "email_from": sender,
            "email_to": "owner@test.invalid",
            "direction": direction,
            "status": status,
            "source": "imap",
            "account_id": cls.account.id,
            "user_id": cls.owner.id,
            "imap_in_inbox": True,
            "imap_folder": "INBOX",
            "imap_uid": uid,
            "message_id_header": "<msg-%s@test.invalid>" % uid,
            "thread_root_id": root,
            "date": "2026-08-1%s 12:00:00" % uid[-1],
            "raw_rfc822": build_rfc822(subject, sender, "owner@test.invalid",
                                       body, attachment),
            "has_attachments": bool(attachment),
            "attachment_count": 1 if attachment else 0,
        }

    def as_owner(self, model="bf.email"):
        """Le modèle vu par le propriétaire de la boîte."""
        return self.env[model].with_user(self.owner)
