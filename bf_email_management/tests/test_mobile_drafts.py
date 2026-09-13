"""Les brouillons du poste, vus et modifiés depuis le téléphone.

Ce qui est éprouvé ici tient en quatre idées : la pile exposée est la BONNE
(pas les envois différés, pas les notes, pas ceux d'un collègue), l'écriture
est PARTIELLE (ce qu'on n'envoie pas n'est pas touché), le conflit se REFUSE
au lieu de se fusionner, et l'envoi emprunte le chemin du noyau.
"""
import json

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import MobileApiCase


@tagged("post_install", "-at_install")
class TestMobileDrafts(MobileApiCase):

    def setUp(self):
        super().setUp()
        self.Scheduled = self.env["mail.scheduled.message"].with_user(self.owner)
        self.Api = self.as_owner()

    # ------------------------------------------------------------ fixtures
    def _draft(self, subject="Brouillon du poste", body="<p>Bonjour.</p>",
               is_draft=True, is_note=False, user=None, partners=None,
               params=None):
        """Un brouillon posé comme le poste le pose : sentinelle + drapeau."""
        user = user or self.owner
        partners = self.partner if partners is None else partners
        # La fiche porteuse appartient à l'auteur : `mail.scheduled.message`
        # refuse la création quand l'auteur ne peut pas écrire dessus.
        porteuse = self.inbound if user == self.owner else self.foreign
        return self.env["mail.scheduled.message"].with_user(user).create({
            "model": "bf.email",
            "res_id": porteuse.id,
            "subject": subject,
            "body": body,
            "author_id": user.partner_id.id,
            "partner_ids": [(6, 0, partners.ids)],
            "scheduled_date": "2031-01-01 12:00:00",
            "bf_is_draft": is_draft,
            "is_note": is_note,
            "notification_parameters": json.dumps(params or {}),
        })

    def _ids(self, payload):
        return [d["id"] for d in payload["drafts"]]

    # ------------------------------------------------------------- portée
    def test_la_liste_rend_mes_brouillons(self):
        draft = self._draft()
        self.assertIn(draft.id, self._ids(self.Api.mobile_drafts()))

    def test_un_envoi_differe_ne_remonte_pas(self):
        """Il partira tout seul à sa date. L'afficher parmi des brouillons,
        sur un écran où « Envoyer » est à un pouce, invite à le devancer."""
        differe = self._draft(subject="Part lundi", is_draft=False)
        self.assertNotIn(differe.id, self._ids(self.Api.mobile_drafts()))

    def test_une_note_interne_ne_remonte_pas(self):
        note = self._draft(subject="Note pour moi", is_note=True)
        self.assertNotIn(note.id, self._ids(self.Api.mobile_drafts()))

    def test_le_brouillon_d_un_collegue_ne_remonte_pas(self):
        """`group_email_admin` lit toutes les boîtes dans l'ORM ; l'API
        mobile l'ignore délibérément — c'est MA boîte sur MON téléphone."""
        autre = self._draft(subject="Pas le mien", user=self.stranger)
        self.assertNotIn(autre.id, self._ids(self.Api.mobile_drafts()))
        with self.assertRaises(UserError):
            self.Api.mobile_draft(autre.id)

    def test_la_recherche_porte_sur_l_objet(self):
        cible = self._draft(subject="Soumission toiture")
        self._draft(subject="Tout autre chose")
        found = self._ids(self.Api.mobile_drafts(search="toiture"))
        self.assertEqual(found, [cible.id])

    def test_has_more_annonce_la_page_suivante(self):
        for i in range(3):
            self._draft(subject="Brouillon %s" % i)
        page = self.Api.mobile_drafts(limit=2)
        self.assertEqual(len(page["drafts"]), 2)
        self.assertTrue(page["has_more"])

    # ------------------------------------------------------------- lecture
    def test_la_fiche_complete_porte_les_deux_corps(self):
        """Le HTML pour le renvoyer intact, le texte pour l'éditer."""
        draft = self._draft(body="<p>Bonjour <b>Jean</b>.</p>")
        data = self.Api.mobile_draft(draft.id)
        self.assertEqual(data["body_html"], "<p>Bonjour <b>Jean</b>.</p>")
        self.assertIn("Jean", data["body_text"])
        self.assertNotIn("<b>", data["body_text"])

    def test_la_copie_conforme_est_rendue_en_lecture(self):
        """`mail.scheduled.message` n'a pas de champ de copie conforme : la
        sienne dort dans `notification_parameters`. Le téléphone doit la
        VOIR, faute de pouvoir la modifier."""
        cc = self.env["res.partner"].create(
            {"name": "Témoin", "email": "temoin@acme.test"})
        draft = self._draft(params={"recipient_cc_ids": cc.ids})
        self.assertIn("Témoin", self.Api.mobile_draft(draft.id)["cc_display"])

    def test_un_destinataire_sans_courriel_n_est_pas_une_pastille_vide(self):
        muet = self.env["res.partner"].create({"name": "Sans adresse"})
        draft = self._draft(partners=self.partner | muet)
        data = self.Api.mobile_draft(draft.id)
        self.assertEqual(data["to"], ["client@acme.test"])
        self.assertIn("Sans adresse", data["to_display"])

    # ----------------------------------------------------- écriture partielle
    def test_ecrire_l_objet_seul_ne_touche_ni_corps_ni_destinataires(self):
        draft = self._draft(body="<p>Texte d'origine.</p>")
        self.Api.mobile_draft_save(draft.id, subject="Nouvel objet")
        self.assertEqual(draft.subject, "Nouvel objet")
        self.assertEqual(draft.body, "<p>Texte d'origine.</p>")
        self.assertEqual(draft.partner_ids, self.partner)

    def test_ecrire_le_corps_seul_ne_touche_pas_l_objet(self):
        draft = self._draft(subject="Objet d'origine")
        self.Api.mobile_draft_save(draft.id, body="Nouveau texte.")
        self.assertEqual(draft.subject, "Objet d'origine")
        self.assertIn("Nouveau texte.", draft.body)

    def test_le_texte_tape_reste_du_texte(self):
        draft = self._draft()
        self.Api.mobile_draft_save(draft.id, body="Merci <script>alert(1)</script>")
        self.assertNotIn("<script>", draft.body)

    def test_la_copie_conforme_survit_a_une_sauvegarde(self):
        """Le téléphone ne peut pas la reconstituer ; il ne doit donc jamais
        réécrire `notification_parameters`."""
        cc = self.env["res.partner"].create(
            {"name": "Témoin", "email": "temoin@acme.test"})
        draft = self._draft(params={"recipient_cc_ids": cc.ids})
        self.Api.mobile_draft_save(draft.id, subject="Modifié au téléphone")
        params = json.loads(draft.notification_parameters)
        self.assertEqual(params["recipient_cc_ids"], cc.ids)

    def test_remplacer_les_destinataires(self):
        draft = self._draft()
        self.Api.mobile_draft_save(draft.id, to=["nouveau@acme.test"])
        self.assertEqual(draft.partner_ids.mapped("email"),
                         ["nouveau@acme.test"])

    def test_un_corps_vide_est_accepte_sur_un_brouillon(self):
        """Un brouillon n'a pas à être valide : c'est ce qui le distingue
        d'un envoi."""
        draft = self._draft()
        self.Api.mobile_draft_save(draft.id, body="   ")
        self.assertFalse(draft.body)

    # ------------------------------------------------------------- conflit
    def _ecriture_concurrente(self, draft, **vals):
        """Ce qu'une AUTRE requête aurait laissé derrière elle.

        ⚠️ Un simple `write` ne suffit pas : Postgres date l'écriture du début
        de la transaction, et le harnais n'en ouvre qu'une. Deux `write`
        successifs portent donc le même `write_date`, et le conflit qu'on veut
        éprouver ne se déclencherait jamais — le test passerait au vert en
        n'ayant rien vérifié.
        """
        draft.write(vals)
        self.env.cr.execute(
            "UPDATE mail_scheduled_message "
            "SET write_date = write_date + interval '1 second' WHERE id = %s",
            (draft.id,))
        draft.invalidate_recordset(["write_date"])

    def test_une_version_perimee_refuse_l_ecriture(self):
        draft = self._draft(subject="Avant")
        version = self.Api.mobile_draft(draft.id)["version"]
        self._ecriture_concurrente(draft, subject="Modifié au poste entretemps")
        result = self.Api.mobile_draft_save(
            draft.id, base_version=version, subject="Modifié au téléphone")
        self.assertTrue(result["conflict"])
        # Rien n'a été écrit, et le refus rapporte ce que le serveur porte.
        self.assertEqual(draft.subject, "Modifié au poste entretemps")
        self.assertEqual(result["draft"]["subject"],
                         "Modifié au poste entretemps")

    def test_la_bonne_version_laisse_passer(self):
        draft = self._draft(subject="Avant")
        version = self.Api.mobile_draft(draft.id)["version"]
        result = self.Api.mobile_draft_save(
            draft.id, base_version=version, subject="Après")
        self.assertTrue(result["ok"])
        self.assertEqual(draft.subject, "Après")

    def test_sans_version_on_ecrit(self):
        """Un client écrit avant cette garde garde le comportement contre
        lequel il a été écrit, et c'est aussi le moyen de forcer après un
        conflit."""
        draft = self._draft(subject="Avant")
        self._ecriture_concurrente(draft, subject="Bougé")
        self.assertTrue(self.Api.mobile_draft_save(draft.id, subject="Après")["ok"])
        self.assertEqual(draft.subject, "Après")

    def test_un_envoi_sur_version_perimee_est_refuse(self):
        draft = self._draft()
        version = self.Api.mobile_draft(draft.id)["version"]
        self._ecriture_concurrente(
            draft, body="<p>Le poste a récrit le texte.</p>")
        result = self.Api.mobile_draft_send(draft.id, base_version=version)
        self.assertTrue(result["conflict"])
        self.assertTrue(draft.exists())

    # --------------------------------------------------------------- envoi
    def test_envoyer_poste_le_message_et_consomme_le_brouillon(self):
        draft = self._draft(body="<p>Voici la réponse.</p>")
        before = self.env["mail.message"].sudo().search_count([
            ("model", "=", "bf.email"), ("res_id", "=", self.inbound.id)])
        self.Api.mobile_draft_send(draft.id)
        after = self.env["mail.message"].sudo().search_count([
            ("model", "=", "bf.email"), ("res_id", "=", self.inbound.id)])
        self.assertEqual(after, before + 1)
        self.assertFalse(draft.exists())

    def test_un_brouillon_sans_destinataire_ne_part_pas(self):
        draft = self._draft(partners=self.env["res.partner"])
        with self.assertRaises(UserError):
            self.Api.mobile_draft_send(draft.id)
        self.assertTrue(draft.exists())

    def test_un_brouillon_vide_ne_part_pas(self):
        draft = self._draft(body="")
        with self.assertRaises(UserError):
            self.Api.mobile_draft_send(draft.id)
        self.assertTrue(draft.exists())

    def test_on_ne_peut_pas_envoyer_le_brouillon_d_un_collegue(self):
        autre = self._draft(user=self.stranger)
        with self.assertRaises(UserError):
            self.Api.mobile_draft_send(autre.id)
        self.assertTrue(autre.exists())

    # ------------------------------------------------------------ jeter
    def test_jeter_un_brouillon(self):
        draft = self._draft()
        self.Api.mobile_draft_delete(draft.id)
        self.assertFalse(draft.exists())

    def test_on_ne_jette_pas_le_brouillon_d_un_collegue(self):
        autre = self._draft(user=self.stranger)
        with self.assertRaises(UserError):
            self.Api.mobile_draft_delete(autre.id)
        self.assertTrue(autre.exists())

    # ------------------------------------------------------- pièces jointes
    def test_un_identifiant_de_piece_non_televerse_est_refuse(self):
        """La garde qui empêche la route d'être un export de toute la base
        par identifiant."""
        draft = self._draft()
        etranger = self.env["ir.attachment"].sudo().create({
            "name": "secret.pdf", "datas": "Yg==",
            "res_model": "res.partner", "res_id": self.partner.id,
        })
        with self.assertRaises(UserError):
            self.Api.mobile_draft_save(
                draft.id, device=self.device,
                attachment_ids=[etranger.id])
        self.assertTrue(etranger.exists())

    def test_une_piece_deja_sur_le_brouillon_se_garde(self):
        draft = self._draft()
        piece = self.env["ir.attachment"].sudo().create({
            "name": "devis.pdf", "datas": "Yg==",
            "res_model": "mail.scheduled.message", "res_id": draft.id,
        })
        draft.sudo().write({"attachment_ids": [(6, 0, piece.ids)]})
        self.Api.mobile_draft_save(
            draft.id, device=self.device, attachment_ids=[piece.id])
        self.assertEqual(draft.attachment_ids, piece)

    def test_une_piece_televersee_est_reparentee_sur_le_brouillon(self):
        """🔴 Laissée sous le marqueur de l'appareil, `_gc_uploads` la
        balaierait après 24 h sous un brouillon qui affiche encore son nom."""
        draft = self._draft()
        staged = self.Api.mobile_stage_upload(
            self.device, filename="photo.png", content=b"x" * 10,
            mimetype="image/png")
        self.Api.mobile_draft_save(
            draft.id, device=self.device,
            attachment_ids=[staged["attachment_id"]])
        piece = self.env["ir.attachment"].sudo().browse(staged["attachment_id"])
        self.assertEqual(piece.res_model, "mail.scheduled.message")
        self.assertEqual(piece.res_id, draft.id)
        self.assertIn(piece, draft.attachment_ids)

    def test_retirer_une_piece_jointe(self):
        draft = self._draft()
        piece = self.env["ir.attachment"].sudo().create({
            "name": "devis.pdf", "datas": "Yg==",
            "res_model": "mail.scheduled.message", "res_id": draft.id,
        })
        draft.sudo().write({"attachment_ids": [(6, 0, piece.ids)]})
        self.Api.mobile_draft_save(draft.id, device=self.device,
                                   attachment_ids=[])
        self.assertFalse(draft.attachment_ids)

    def test_la_date_est_rendue_en_utc(self):
        """🔴 Une date Odoo est NAÏVE et en UTC ; `.timestamp()` d'une naïve
        l'interprète dans le fuseau du serveur. Le brouillon se serait affiché
        « il y a 4 heures » au moment même où on le quittait."""
        import pytz
        draft = self._draft()
        attendu = int(
            draft.write_date.replace(tzinfo=pytz.UTC).timestamp() * 1000)
        self.assertEqual(self.Api.mobile_draft(draft.id)["saved_ms"], attendu)

    # ------------------------------------------------------------- config
    def test_la_capacite_est_annoncee(self):
        self.assertTrue(self.Api.get_mobile_config()["server_drafts"])
