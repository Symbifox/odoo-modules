"""Les en-têtes bruts des lignes de la passerelle et du chatter.

Mesuré sur une boîte réelle : la quasi-totalité des lignes du chatter et la
plupart de celles de la passerelle n'avaient aucun en-tête. Ni la projection depuis
``mail.message`` ni les rattrapages du miroir IMAP ne les écrivaient, alors
que le message venait d'être lu. Et la passerelle gardait le pliage de
l'objet, et perdait les lettres d'un objet 8 bits.
"""
import base64
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged

from ..models import bf_email_imap as imap
from .common import MobileApiCase
from .test_imap_folders_and_uid_guard import FakeImap

IMAP = "odoo.addons.bf_email_management.models.bf_email_imap"

ENTETES_SEEK = (
    b"Received: from mx.seek.test\r\n by mx.example.test; Thu, 1 Oct 2026\r\n"
    b"Authentication-Results: mx.example.test; dkim=pass header.d=seek.test\r\n"
    b"Subject: Application update for Intermediate or Senior Resource Consent"
    b"\r\n Planner at The Planning Collective\r\n"
    b"From: =?utf-8?q?SEEK_Avis?=\r\n <noreply@seek.test>\r\n"
    b"List-Unsubscribe: <https://seek.test/u?x=1>,\r\n <mailto:u@seek.test>\r\n"
    b"List-Unsubscribe-Post: List-Unsubscribe=One-Click\r\n"
    b"Message-ID: <%s>\r\n\r\n"
)


def message_brut(message_id, extra=b"", subject=b"Objet simple"):
    return (
        b"From: Client <client@acme.test>\r\n"
        b"To: owner@test.invalid\r\n"
        b"Subject: " + subject + b"\r\n"
        b"Message-ID: " + message_id.encode() + b"\r\n"
        b"Date: Thu, 01 Oct 2026 10:00:00 -0400\r\n" + extra +
        b"\r\ncorps\r\n"
    )


class FakeImapEntetes(FakeImap):
    """``FakeImap`` qui rend aussi les en-têtes COMPLETS (``BODY.PEEK[HEADER]``).

    ``full`` : ``{dossier: {uid: octets de l'en-tête}}``. Sans entrée, un UID
    rend un en-tête minimal portant son Message-ID.
    """

    def __init__(self, *args, full=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.full = full or {}
        self.readonly = []

    def select(self, mailbox, readonly=False):
        self.readonly.append(readonly)
        return super().select(mailbox, readonly=readonly)

    def uid(self, command, *args):
        if command == "FETCH" and "BODY.PEEK[HEADER])" in str(args[-1]):
            self.calls.append((command,) + args)
            box = self._current()
            out = []
            for uid in str(args[0]).split(","):
                mid = box.get(uid)
                if mid is None:
                    continue
                body = self.full.get(self.selected, {}).get(uid) or (
                    "Message-ID: %s\r\nSubject: x\r\n\r\n" % mid).encode()
                out.append((b"1 (UID %s BODY[HEADER] {%d}" % (
                    uid.encode(), len(body)), body))
            out.append(b")")
            return ("OK", out)
        return super().uid(command, *args)


@tagged("post_install", "-at_install")
class TestFonctionsEntetes(TransactionCase):
    """La fonction commune : dépliage, RFC 2047, 8 bits, pas de NUL."""

    def test_folded_values_come_out_on_one_line(self):
        texte = imap.headers_text_from_bytes(ENTETES_SEEK % b"a@b")
        self.assertIn(
            "Subject: Application update for Intermediate or Senior Resource "
            "Consent Planner at The Planning Collective", texte)
        self.assertIn("From: SEEK Avis <noreply@seek.test>", texte)
        self.assertIn("Message-ID: <a@b>", texte)
        self.assertNotIn("\r", texte)
        for ligne in texte.splitlines():
            self.assertFalse(ligne[:1].isspace(), ligne)

    def test_repeated_headers_are_all_kept_in_order(self):
        texte = imap.headers_text_from_bytes(
            b"Received: from a\r\nReceived: from b\r\nSubject: s\r\n\r\n")
        self.assertEqual(texte.splitlines()[:2],
                         ["Received: from a", "Received: from b"])

    def test_raw_utf8_and_raw_latin1_keep_their_letters(self):
        texte = imap.headers_text_from_bytes(
            b"Subject: Offre \xe2\x80\x93 r\xc3\xb4le\r\n"
            b"X-Latin: Donn\xe9es re\xe7ues\r\n"
            b"X-Mix: caf\xc3\xa9 et \xe9t\xe9\r\n\r\n")
        self.assertIn("Subject: Offre – rôle", texte)
        self.assertIn("X-Latin: Données reçues", texte)
        self.assertIn("X-Mix: café et été", texte)
        self.assertNotIn("�", texte)

    def test_unknown_8bit_words_written_by_odoo_are_read_back(self):
        texte = imap.headers_text_from_bytes(
            b"Subject: Offre pour =?unknown-8bit?q?Donn=E9es?=\r\n"
            b"X-Mix: =?unknown-8bit?q?caf=C3=A9?= et =?utf-8?q?=C3=A9t=C3=A9?=\r\n"
            b"\r\n")
        self.assertIn("Subject: Offre pour Données", texte)
        self.assertIn("X-Mix: café et été", texte)

    def test_hostile_encoded_words_never_leave_surrogates(self):
        # `unicode_escape` fabrique un surrogate que Postgres refuse ; un jeu
        # de caractères inconnu ne doit pas vider tout le bloc.
        texte = imap.headers_text_from_bytes(
            b"Subject: =?unknown-8bit?q?a?= =?unicode_escape?q?=5Cud800?=\r\n"
            b"X-Autre: =?unknown-8bit?q?b?= =?undefined?q?x?=\r\n"
            b"From: Client <client@acme.test>\r\n\r\n")
        self.assertIn("From: Client <client@acme.test>", texte)
        self.assertFalse(any(0xD800 <= ord(c) <= 0xDFFF for c in texte))
        texte.encode("utf-8")

    def test_unicode_line_separators_cannot_add_a_header_line(self):
        texte = imap.headers_text_from_bytes(
            b"Subject: =?utf-8?q?a=E2=80=A8List-Unsubscribe:_<https://x>?=\r\n"
            b"\r\n")
        self.assertEqual(len(texte.splitlines()), 1)
        self.assertEqual(imap.header_from_text(texte, "List-Unsubscribe"), "")

    def test_an_unknown_8bit_sender_keeps_its_address(self):
        msg = imap.parse_rfc822(
            b"From: =?unknown-8bit?q?Andr=E9?= <andre@acme.test>\r\n\r\n")
        self.assertEqual(imap.first_header(msg, "From"),
                         "André <andre@acme.test>")

    def test_a_malformed_folded_value_still_comes_out_on_one_line(self):
        # L'analyse refuse ce Message-ID : la valeur brute prend le relais,
        # pli compris, et doit sortir dépliée.
        texte = imap.headers_text_from_bytes(
            b"Message-ID: <<<>>\r\n suite\r\nSubject: s\r\n\r\n")
        self.assertEqual(texte.splitlines(),
                         ["Message-ID: <<<>> suite", "Subject: s"])

    def test_the_last_net_removes_any_surrogate(self):
        self.assertEqual(imap._clean_value("a\ud800b\udcff"),
                         "a\ufffdb\ufffd")

    def test_nul_bytes_never_reach_the_database(self):
        self.assertEqual(imap.headers_text_from_bytes(b"Subject: a\x00b\r\n\r\n"),
                         "Subject: ab")

    def test_the_body_is_never_read_as_headers(self):
        texte = imap.headers_text_from_bytes(
            b"Subject: s\n\nX-Pas-Un-Entete: corps\n")
        self.assertEqual(texte, "Subject: s")

    def test_garbage_gives_empty_text_not_an_error(self):
        self.assertEqual(imap.headers_text_from_bytes(b""), "")
        self.assertEqual(imap.headers_text_from_bytes(None), "")
        imap.headers_text_from_bytes(b"\xff\xfe\x00:::\r\n\r\n")

    def test_mojibake_is_repaired_only_when_it_really_is_mojibake(self):
        self.assertEqual(imap.repair_mojibake("Trier par DerniÃ¨re mis a jour"),
                         "Trier par Dernière mis a jour")
        self.assertEqual(imap.repair_mojibake("Ãa va"), "Ãa va")
        self.assertEqual(imap.repair_mojibake("Déjà vu"), "Déjà vu")
        self.assertEqual(imap.repair_mojibake(""), "")

    def test_first_header_recovers_latin1(self):
        msg = imap.parse_rfc822(b"Subject: Donn\xe9es\r\n\r\nx")
        self.assertEqual(str(msg.get("Subject")), "Donn�es")
        self.assertEqual(imap.first_header(msg, "subject"), "Données")
        self.assertEqual(imap.first_header(msg, "Cc"), "")


@tagged("post_install", "-at_install")
class TestProjectionPasserelle(MobileApiCase):
    """La projection depuis ``mail.message`` lit l'original joint."""

    def setUp(self):
        super().setUp()
        Server = self.env["fetchmail.server"].sudo().with_context(
            active_test=False)
        Server.search([]).write({"active": False})
        self.server = Server.create({
            "name": "Réception de test", "server": "imap.test.invalid",
            "server_type": "imap", "original": True, "active": True,
        })

    def _message(self, message_id, original=None, forged=None,
                 subject="Objet", message_type="email"):
        # L'ordre d'Odoo : l'original d'abord, les pièces du message ensuite
        # (`_message_parse_extract_payload`, un seul `create`).
        # La passerelle écrit `description` = nom sur chaque pièce.
        attachments = self.env["ir.attachment"]
        if original:
            attachments |= self.env["ir.attachment"].create({
                "name": "original_email.eml", "raw": original,
                "description": "original_email.eml"})
        if forged:
            attachments |= self.env["ir.attachment"].create({
                "name": "original_email.eml", "raw": forged,
                "description": "original_email.eml"})
        return self.env["mail.message"].create({
            "model": "res.partner", "res_id": self.partner.id,
            "message_type": message_type, "subject": subject,
            "message_id": message_id, "body": "<p>corps</p>",
            "author_id": self.partner.id, "email_from": "client@acme.test",
            "attachment_ids": [(6, 0, attachments.ids)],
        })

    def _projeter(self, msg):
        return self.env["bf.email"].with_user(self.owner)._prepare_email_vals(msg)

    def test_headers_come_from_the_kept_original(self):
        msg = self._message("<seek-1@test>",
                            original=ENTETES_SEEK % b"seek-1@test")
        vals = self._projeter(msg)
        self.assertIn("List-Unsubscribe-Post: List-Unsubscribe=One-Click",
                      vals["raw_headers"])
        self.assertIn("dkim=pass", vals["raw_headers"])

    def test_without_original_there_are_no_headers(self):
        self.assertEqual(self._projeter(self._message("<sans@test>"))["raw_headers"], "")

    def test_an_original_for_another_message_is_ignored(self):
        msg = self._message("<moi@test>", original=ENTETES_SEEK % b"autre@test")
        self.assertEqual(self._projeter(msg)["raw_headers"], "")

    def test_a_forged_original_sent_by_the_sender_is_not_read(self):
        # L'expéditeur joint son propre « original_email.eml », avec SON
        # Message-ID et de faux résultats d'authentification. Odoo joint le
        # vrai AVANT.
        faux = (b"Authentication-Results: x; spf=pass dkim=pass\r\n"
                b"Message-ID: <vrai@test>\r\n\r\n")
        vrai = message_brut("<vrai@test>",
                            extra=b"Authentication-Results: mx; spf=fail\r\n")
        msg = self._message("<vrai@test>", original=vrai, forged=faux)
        entetes = self._projeter(msg)["raw_headers"]
        self.assertIn("spf=fail", entetes)
        self.assertNotIn("dkim=pass", entetes)

    def test_the_genuine_original_wins_over_later_reencoded_copies(self):
        # Mesuré sur une boîte réelle : une réponse mailersend porte quatre « originaux »,
        # le premier est le vrai, les suivants sont réencodés sans Message-ID.
        vrai = message_brut("<mailersend@test>", extra=b"X-Vrai: oui\r\n")
        msg = self._message("<mailersend@test>", original=vrai,
                            forged=b"MIME-Version: 1.0\r\n"
                                   b"Content-Type: text/plain\r\n\r\nVW1W")
        self.assertIn("X-Vrai: oui", self._projeter(msg)["raw_headers"])

    def test_classer_an_imap_row_never_trusts_the_senders_original(self):
        # « Classer » une ligne IMAP crée les pièces du courriel sans
        # description, puis poste le message en `email` : une pièce
        # « original_email.eml » de l'expéditeur y est la seule de ce nom.
        faux = message_brut("<classe@test>",
                            extra=b"Authentication-Results: x; dkim=pass\r\n")
        piece = self.env["ir.attachment"].create({
            "name": "original_email.eml", "raw": faux})
        msg = self._message("<classe@test>")
        msg.attachment_ids = [(6, 0, piece.ids)]
        self.assertEqual(self._projeter(msg)["raw_headers"], "")

    def test_the_original_must_be_the_very_first_attachment(self):
        avant = self.env["ir.attachment"].create({
            "name": "facture.pdf", "raw": b"%PDF", "description": "facture.pdf"})
        msg = self._message("<second@test>",
                            original=message_brut("<second@test>"))
        msg.attachment_ids = [(6, 0, (avant | msg.attachment_ids).ids)]
        self.assertEqual(self._projeter(msg)["raw_headers"], "")

    def test_imap_promotion_keeps_the_headers_of_odoos_original(self):
        original = message_brut("<promo2@test>",
                                extra=b"X-Source: original\r\n")
        msg = self._message("<promo2@test>", original=original)
        msg.partner_ids = [(4, self.owner.partner_id.id)]
        self.env["bf.email"].with_user(self.owner)._ingest_rfc822(
            message_brut("<promo2@test>", extra=b"X-Source: imap\r\n"),
            562, "INBOX", self.account)
        row = self.env["bf.email"].sudo().search(
            [("message_id_header", "=", "<promo2@test>"),
             ("user_id", "=", self.owner.id)])
        self.assertIn("X-Source: original", row.raw_headers)

    def test_a_lone_forged_original_is_never_read(self):
        # Un serveur actif ne garde pas l'original : la seule pièce de ce nom
        # peut être celle de l'expéditeur.
        self.server.original = False
        faux = message_brut("<seul@test>",
                            extra=b"Authentication-Results: x; dkim=pass\r\n")
        msg = self._message("<seul@test>", original=faux)
        self.assertEqual(self._projeter(msg)["raw_headers"], "")

    def test_latin1_subject_survives_the_original_odoo_writes(self):
        # Odoo garde `message.as_string()` d'un message lu en politique SMTP :
        # le latin-1 brut y devient `=?unknown-8bit?q?…?=`.
        import email as email_mod
        import email.policy
        brut = message_brut("<smtp@test>", subject=b"Offre pour Donn\xe9es")
        original = email_mod.message_from_bytes(
            brut, policy=email.policy.SMTP).as_string().encode()
        msg = self._message("<smtp@test>", original=original,
                            subject="Offre pour Donn\ufffdes")
        vals = self._projeter(msg)
        self.assertEqual(vals["subject"], "Offre pour Données")
        self.assertIn("Subject: Offre pour Données", vals["raw_headers"])

    def test_a_chatter_note_reads_no_attachment(self):
        msg = self._message("<note@test>", original=ENTETES_SEEK % b"note@test",
                            message_type="comment")
        self.assertEqual(self._projeter(msg)["raw_headers"], "")

    def test_a_folded_subject_is_unfolded(self):
        msg = self._message("<pli@test>",
                            subject="Application update for Senior\n Planner")
        self.assertEqual(self._projeter(msg)["subject"],
                         "Application update for Senior Planner")

    def test_a_subject_that_lost_letters_is_read_again_in_the_original(self):
        original = message_brut("<fffd@test>", subject=b"Offre pour Donn\xe9es")
        msg = self._message("<fffd@test>", original=original,
                            subject="Offre pour Donn�es")
        self.assertEqual(self._projeter(msg)["subject"], "Offre pour Données")

    def test_mojibake_subject_is_repaired(self):
        msg = self._message("<moji@test>", subject="Trier par DerniÃ¨re")
        self.assertEqual(self._projeter(msg)["subject"], "Trier par Dernière")

    def test_folded_sender_is_unfolded(self):
        msg = self._message("<exp@test>")
        msg.email_from = "SEEK Avis\n <noreply@seek.test>"
        self.assertEqual(self._projeter(msg)["email_from"],
                         "SEEK Avis <noreply@seek.test>")
        msg.email_from = "Jobs via SEEK\n\t<noreply@seek.test>"
        self.assertEqual(self._projeter(msg)["email_from"],
                         "Jobs via SEEK <noreply@seek.test>",
                         "un pli en tabulation devient une espace")


@tagged("post_install", "-at_install")
class TestRattrapageDuMiroir(MobileApiCase):
    """Les trois chemins du miroir qui retrouvent une ligne existante."""

    def _ligne_passerelle(self, message_id, **extra):
        vals = {
            "subject": "Ligne de la passerelle", "email_from": "client@acme.test",
            "direction": "in", "status": "new", "source": "gateway",
            "user_id": self.owner.id, "message_id_header": message_id,
            "date": "2026-10-01 12:00:00",
        }
        vals.update(extra)
        return self.env["bf.email"].sudo().create(vals)

    def test_ingest_backfills_headers_of_an_existing_row(self):
        row = self._ligne_passerelle("<gw-1@test>")
        created = self.env["bf.email"].with_user(self.owner)._ingest_rfc822(
            message_brut("<gw-1@test>", extra=b"X-Signal: oui\r\n"),
            555, "Archives/2026", self.account)
        self.assertFalse(created)
        self.assertIn("X-Signal: oui", row.raw_headers)
        self.assertEqual(row.imap_uid, "555")

    def test_ingest_refuses_headers_from_another_sender(self):
        # Un tiers qui connaît le Message-ID (copie, liste) livre un AUTRE
        # message sous le même : ses en-têtes ne vont pas sur la ligne.
        row = self._ligne_passerelle("<tiers@test>",
                                     email_from="client@acme.test")
        brut = message_brut("<tiers@test>").replace(
            b"From: Client <client@acme.test>", b"From: Tiers <x@ailleurs.test>")
        self.env["bf.email"].with_user(self.owner)._ingest_rfc822(
            brut, 560, "INBOX", self.account)
        self.assertFalse(row.raw_headers)
        self.assertEqual(row.imap_uid, "560", "le reste du rattrapage suit")

    def test_ingest_accepts_odoo_sending_on_behalf_of_the_author(self):
        # Une notification part de « Auteur <notifications@domaine maison> ».
        domaine = (self.env.company.email or "x@maison.test").rpartition("@")[2]
        if not self.env.company.email:
            self.env.company.email = "info@" + domaine
        row = self._ligne_passerelle("<notif@test>",
                                     email_from="client@acme.test")
        brut = message_brut("<notif@test>").replace(
            b"From: Client <client@acme.test>",
            b"From: Client <notifications@" + domaine.encode() + b">")
        self.env["bf.email"].with_user(self.owner)._ingest_rfc822(
            brut, 561, "INBOX", self.account)
        self.assertTrue(row.raw_headers)

    def test_ingest_never_overwrites_headers_already_there(self):
        row = self._ligne_passerelle("<gw-2@test>", raw_headers="X-Avant: 1")
        self.env["bf.email"].with_user(self.owner)._ingest_rfc822(
            message_brut("<gw-2@test>"), 556, "INBOX", self.account)
        self.assertEqual(row.raw_headers, "X-Avant: 1")

    def test_promotion_from_an_odoo_message_keeps_the_delivered_headers(self):
        self.env["mail.message"].create({
            "model": "res.partner", "res_id": self.partner.id,
            "message_type": "email", "subject": "Promu",
            "message_id": "<promu@test>", "body": "<p>corps</p>",
            "author_id": self.partner.id, "email_from": "client@acme.test",
            "partner_ids": [(4, self.owner.partner_id.id)],
        })
        self.env["bf.email"].with_user(self.owner)._ingest_rfc822(
            message_brut("<promu@test>", extra=b"X-Livre: oui\r\n"),
            557, "INBOX", self.account)
        row = self.env["bf.email"].sudo().search(
            [("message_id_header", "=", "<promu@test>"),
             ("user_id", "=", self.owner.id)])
        self.assertEqual(len(row), 1)
        self.assertIn("X-Livre: oui", row.raw_headers)

    def test_adoption_of_an_inbox_uid_writes_the_headers(self):
        row = self._ligne_passerelle("<adopte@test>",
                                     email_from="noreply@seek.test")
        fake = FakeImapEntetes(
            inbox={"101": self.inbound.message_id_header,
                   "700": "<adopte@test>"},
            full={"INBOX": {"700": ENTETES_SEEK % b"adopte@test"}})
        with patch(f"{IMAP}.open_connection", return_value=fake):
            self.env["bf.email"]._cron_imap_mirror()
        self.assertEqual(row.imap_uid, "700")
        self.assertIn("List-Unsubscribe-Post", row.raw_headers)
        self.assertTrue(row.unsubscribe_one_click,
                        "les signaux tirés des en-têtes suivent")
        self.assertEqual(
            row.subject, "Ligne de la passerelle",
            "un objet sain n'est pas remplacé par celui des en-têtes")

    def test_adoption_checks_the_message_id_before_writing(self):
        row = self._ligne_passerelle("<adopte-2@test>",
                                     email_from="noreply@seek.test")
        fake = FakeImapEntetes(
            inbox={"701": "<adopte-2@test>"},
            full={"INBOX": {"701": ENTETES_SEEK % b"un-autre@test"}})
        with patch(f"{IMAP}.open_connection", return_value=fake):
            self.env["bf.email"]._cron_imap_mirror()
        self.assertFalse(row.raw_headers)


@tagged("post_install", "-at_install")
class TestRattrapageDeLExistant(MobileApiCase):
    """``_bf_rattraper_entetes`` : sur place, par UID, par balayage."""

    def setUp(self):
        super().setUp()
        # La base d'essai peut porter des données réelles : la passe ne doit voir que les
        # lignes que chaque essai prépare.
        self.Rows = self.env["bf.email"].sudo()
        self.mes_lignes = self.Rows.browse()

    def _ligne(self, message_id, **extra):
        vals = {
            "subject": "Ligne", "email_from": "client@acme.test",
            "direction": "in", "status": "read", "source": "chatter",
            "user_id": self.owner.id, "message_id_header": message_id,
            "date": "2026-09-01 12:00:00", "is_handled": True,
            "imap_in_inbox": False,
        }
        vals.update(extra)
        row = self.Rows.create(vals)
        self.mes_lignes |= row
        return row

    def _passe(self, fake, **kwargs):
        kwargs.setdefault("ids", self.mes_lignes.ids)
        with patch(f"{IMAP}.open_connection", return_value=fake):
            return self.Rows._bf_rattraper_entetes(**kwargs)

    def test_local_copy_first_without_any_network(self):
        row = self._ligne("<local@test>", source="imap", raw_rfc822=base64.b64encode(
            message_brut("<local@test>", extra=b"X-Local: 1\r\n")).decode())
        fake = FakeImapEntetes()
        stats = self._passe(fake)
        self.assertIn("X-Local: 1", row.raw_headers)
        self.assertEqual(stats["sur_place"], 1)
        self.assertFalse(fake.calls, "rien à demander au serveur")

    def test_by_uid_in_its_own_folder(self):
        row = self._ligne("<uid@test>", account_id=self.account.id,
                          imap_folder="Archives/2026", imap_uid="42",
                          email_from="noreply@seek.test")
        fake = FakeImapEntetes(
            mailboxes={"Archives/2026": {"42": "<uid@test>"}},
            full={"Archives/2026": {"42": ENTETES_SEEK % b"uid@test"}})
        stats = self._passe(fake)
        self.assertIn("List-Unsubscribe", row.raw_headers)
        self.assertEqual(stats["par_uid"], 1)

    def test_a_stale_uid_is_never_trusted_then_the_sweep_finds_it(self):
        # L'UID 42 d'Archives/2026 porte maintenant un AUTRE message : la
        # ligne a été déplacée depuis. L'UID ne suffit pas ; le balayage la
        # retrouve dans Archives/2025 sous l'UID 7.
        row = self._ligne("<deplace@test>", account_id=self.account.id,
                          imap_folder="Archives/2026", imap_uid="42",
                          email_from="noreply@seek.test")
        fake = FakeImapEntetes(
            mailboxes={"Archives/2026": {"42": "<autre@test>"},
                       "Archives/2025": {"7": "<deplace@test>"}},
            folders=[b'(\\HasNoChildren) "/" "Archives/2026"',
                     b'(\\HasNoChildren) "/" "Archives/2025"'],
            full={"Archives/2026": {"42": ENTETES_SEEK % b"autre@test"},
                  "Archives/2025": {"7": ENTETES_SEEK % b"deplace@test"}})
        stats = self._passe(fake)
        self.assertFalse(row.raw_headers, "sans balayage, l'UID périmé échoue")
        self.assertEqual(stats["sans_source"], 1)
        stats = self._passe(fake, balayer=True)
        self.assertIn("<deplace@test>", row.raw_headers)
        self.assertEqual(stats["par_balayage"], 1)
        self.assertEqual(row.imap_folder, "Archives/2026",
                         "le repère du miroir n'est pas touché")
        self.assertEqual(row.imap_uid, "42")

    def test_the_server_is_only_read(self):
        self._ligne("<lu@test>", account_id=self.account.id,
                    imap_folder="INBOX", imap_uid="9")
        fake = FakeImapEntetes(inbox={"9": "<lu@test>"},
                               folders=[b'(\\HasNoChildren) "/" "INBOX"'])
        self._passe(fake, balayer=True)
        self.assertTrue(fake.readonly)
        self.assertTrue(all(fake.readonly), "chaque SELECT en lecture seule")
        for appel in fake.calls:
            self.assertNotIn(appel[0], ("STORE", "COPY", "MOVE", "EXPUNGE"))
            if appel[0] == "FETCH":
                self.assertIn("PEEK", appel[-1])

    def test_a_row_that_has_headers_is_never_rewritten(self):
        row = self._ligne("<garde@test>", account_id=self.account.id,
                          raw_headers="X-Avant: 1")
        fake = FakeImapEntetes(inbox={"4": "<garde@test>"},
                               full={"INBOX": {"4": message_brut("<garde@test>")}})
        fake.select("INBOX", readonly=True)
        self.Rows._bf_ecrire_entetes_imap(fake, {4: row}, self.account)
        self.assertEqual(row.raw_headers, "X-Avant: 1")

    def test_the_sweep_keeps_the_first_copy_of_a_message(self):
        row = self._ligne("<double@test>", account_id=self.account.id)
        fake = FakeImapEntetes(
            mailboxes={"Archives/2026": {"5": "<double@test>",
                                         "9": "<double@test>"}},
            folders=[b'(\\HasNoChildren) "/" "Archives/2026"'],
            full={"Archives/2026": {
                "5": message_brut("<double@test>", extra=b"X-Copie: 5\r\n"),
                "9": message_brut("<double@test>", extra=b"X-Copie: 9\r\n")}})
        self._passe(fake, balayer=True)
        self.assertIn("X-Copie: 5", row.raw_headers)

    def test_unfolding_the_sender_keeps_the_category(self):
        row = self._ligne("<cat@test>", email_from="Avis\n <noreply@x.test>",
                          raw_headers="X-Rien: 1")
        row.category = "client"
        self.Rows._bf_reparer_objets()
        self.assertEqual(row.email_from, "Avis <noreply@x.test>")
        self.assertEqual(row.category, "client")

    def test_a_row_without_any_source_is_counted_not_invented(self):
        row = self._ligne("<rien@test>")
        stats = self._passe(FakeImapEntetes(), balayer=True)
        self.assertFalse(row.raw_headers)
        self.assertEqual(stats["sans_source"], 1)

    def test_a_second_pass_does_nothing(self):
        self._ligne("<deux@test>", source="imap", raw_rfc822=base64.b64encode(
            message_brut("<deux@test>")).decode())
        self._passe(FakeImapEntetes())
        stats = self._passe(FakeImapEntetes())
        self.assertEqual(stats["vues"], 0)

    def test_a_network_failure_does_not_lose_the_local_work(self):
        local = self._ligne("<ok@test>", source="imap", raw_rfc822=base64.b64encode(
            message_brut("<ok@test>")).decode())
        self._ligne("<reseau@test>", account_id=self.account.id,
                    imap_folder="INBOX", imap_uid="3")
        with patch(f"{IMAP}.open_connection", side_effect=OSError("panne")):
            stats = self.Rows._bf_rattraper_entetes(
                balayer=True, ids=self.mes_lignes.ids)
        self.assertTrue(local.raw_headers)
        self.assertEqual(stats["sans_source"], 1)

    def test_the_night_cron_only_looks_at_the_week(self):
        vieille = self._ligne("<vieille@test>", source="imap",
                              raw_rfc822=base64.b64encode(
                                  message_brut("<vieille@test>")).decode())
        self.env.cr.execute(
            "UPDATE bf_email SET create_date = now() - interval '30 days' "
            "WHERE id = %s", [vieille.id])
        vieille.invalidate_recordset()
        recente = self._ligne("<recente@test>", source="imap",
                              raw_rfc822=base64.b64encode(
                                  message_brut("<recente@test>")).decode())
        with patch(f"{IMAP}.open_connection", return_value=FakeImapEntetes()):
            self.Rows._cron_rattraper_entetes()
        self.assertFalse(vieille.raw_headers)
        self.assertTrue(recente.raw_headers)

    def test_subjects_are_repaired_where_headers_already_exist(self):
        plie = self._ligne("<plie@test>", subject="Senior\n Planner",
                           raw_headers="Subject: Senior Planner")
        perdu = self._ligne("<perdu@test>", subject="Donn�es",
                            raw_headers="Subject: Données")
        moji = self._ligne("<moji2@test>", subject="DerniÃ¨re",
                           raw_headers="X-Rien: 1")
        sain = self._ligne("<sain@test>", subject="Déjà vu",
                           raw_headers="Subject: autre chose")
        n = self.Rows._bf_reparer_objets()
        self.assertEqual(plie.subject, "Senior Planner")
        self.assertEqual(perdu.subject, "Données")
        self.assertEqual(moji.subject, "Dernière")
        self.assertEqual(sain.subject, "Déjà vu")
        # La base d'essai peut porter d'autres objets pliés.
        self.assertGreaterEqual(n, 3)


@tagged("post_install", "-at_install")
class TestReglesSurEnTete(MobileApiCase):
    """Choix délibéré : une condition sur en-tête ne voit
    pas les courriels de la passerelle ni du chatter, même avec leurs en-têtes.
    """

    def _regle_list_unsubscribe(self):
        return self.env["bf.email.rule"].sudo().create({
            "name": "List-Unsubscribe présent", "scope": "user",
            "user_id": self.owner.id, "match_type": "all",
            "condition_ids": [(0, 0, {
                "kind": "condition", "field_name": "header",
                "header_name": "List-Unsubscribe", "operator": "is_set"})],
        })

    def _ligne(self, source):
        return self.env["bf.email"].sudo().create({
            "subject": "Offre", "email_from": "noreply@seek.test",
            "direction": "in", "status": "new", "source": source,
            "user_id": self.owner.id,
            "message_id_header": "<regle-%s@test>" % source,
            "date": "2026-10-01 12:00:00",
            "raw_headers": "List-Unsubscribe: <https://seek.test/u>",
        })

    def test_gateway_and_chatter_rows_are_not_seen_by_header_rules(self):
        regle = self._regle_list_unsubscribe()
        ctx = self.env["bf.email"]._rule_owner_context(self.owner)
        for source in ("gateway", "chatter"):
            self.assertFalse(regle._match(self._ligne(source), ctx), source)

    def test_imap_rows_still_are(self):
        regle = self._regle_list_unsubscribe()
        ctx = self.env["bf.email"]._rule_owner_context(self.owner)
        self.assertTrue(regle._match(self._ligne("imap"), ctx))

    def test_the_signals_still_read_them(self):
        ligne = self._ligne("gateway")
        self.assertTrue(ligne.is_bulk)
        self.assertEqual(ligne.unsubscribe_url, "https://seek.test/u")
