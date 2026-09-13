"""Le livrable remis, éprouvé sur une base qui se fédère avec elle-même.

Même montage que le socle : le pair A invite, le pair B accepte, et tout ce qui
part de B arrive chez A par la vraie porte HTTP signée.
"""
import base64

from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.bf_federation.tests.test_federation import TestFederation


@tagged("post_install", "-at_install", "federation", "federation_document")
class TestFederationDocument(TestFederation):

    def _remettre(self, nom="Politique de confidentialité", version="1.0", contenu=b"PDF-1"):
        att = self.env["ir.attachment"].create({
            "name": "politique.pdf", "raw": contenu, "mimetype": "application/pdf"})
        doc = self.env["federation.document"].create({
            "name": nom, "reference": "POL-004", "version": version,
            "summary": "Ce que la maison fait des renseignements personnels.",
            "attachment_ids": [(6, 0, att.ids)],
            "peer_partner_id": self.peer_b.partner_id.id,
            "federation_peer_id": self.peer_b.id,
        })
        self._flush()
        return doc

    def _miroir(self, doc):
        link = self.env["federation.link"].with_context(active_test=False).search(
            [("peer_id", "=", self.peer_a.id), ("remote_ref", "=", str(doc.id)),
             ("res_model", "=", "federation.document")], limit=1)
        self.assertTrue(link, "aucun miroir pour %s" % doc.name)
        return link._record().with_context(active_test=False)

    # --- Le genre est annoncé -------------------------------------------------------
    def test_d01_le_genre_est_annonce_au_pair(self):
        self.assertTrue(self.peer_a.action_ping())
        self.peer_a.invalidate_recordset()
        annonces = (self.peer_a.accepted_kinds or "").split(",")
        self.assertIn("document.share", annonces)
        self.assertIn("document.ack", annonces)
        self.assertIn("task.share", annonces, "le genre du socle reste annoncé")
        self.assertTrue(self.peer_a.accepts("document.share"))
        self.assertFalse(self.peer_a.accepts("licorne.share"), "un genre inconnu n'est pas accepté")

    def test_d02_un_pair_muet_reste_permissif(self):
        """Un pair d'une version antérieure n'annonce rien : on n'empêche alors rien."""
        self.peer_a.sudo().write({"accepted_kinds": False})
        self.assertTrue(self.peer_a.accepts("document.share"))

    # --- La remise -------------------------------------------------------------------
    def test_d03_remise_cree_le_miroir_avec_le_fichier(self):
        doc = self._remettre()
        miroir = self._miroir(doc)
        self.assertEqual(miroir.name, "Politique de confidentialité")
        self.assertEqual(miroir.reference, "POL-004")
        self.assertEqual(miroir.version, "1.0")
        self.assertEqual(miroir.federation_origin, "remote")
        self.assertEqual(len(miroir.attachment_ids), 1)
        self.assertEqual(base64.b64decode(miroir.attachment_ids.datas), b"PDF-1")
        self.assertFalse(miroir.file_note)
        self.assertFalse(miroir.acknowledged)
        self.assertIn("Ce que la maison fait", miroir.summary)

    def test_d04_fichier_trop_gros_reste_chez_l_emetteur(self):
        self.peer_b.sudo().write({"attachment_limit_mb": 1})
        gros = b"x" * (1024 * 1024 + 10)
        doc = self._remettre(contenu=gros)
        miroir = self._miroir(doc)
        self.assertFalse(miroir.attachment_ids, "le fichier n'a pas traversé")
        self.assertTrue(miroir.file_note, "et le miroir le dit")
        self.assertIn("politique.pdf", miroir.file_note)

    def test_d05_l_accuse_remonte_et_nomme_qui_a_lu(self):
        doc = self._remettre()
        miroir = self._miroir(doc)
        miroir.with_user(self.receveur).action_acknowledge()
        self._flush()
        doc.invalidate_recordset()
        self.assertTrue(doc.acknowledged)
        self.assertEqual(doc.acknowledged_by, "Personne du pair")
        self.assertTrue(doc.acknowledged_on)
        notes = self.env["mail.message"].search(
            [("model", "=", "federation.document"), ("res_id", "=", doc.id), ("body", "ilike", "accusé réception")])
        self.assertEqual(len(notes), 1)

    def test_d06_un_livrable_recu_ne_se_re_accuse_pas_ni_ne_s_accuse_a_l_envers(self):
        doc = self._remettre()
        miroir = self._miroir(doc)
        with self.assertRaises(UserError):
            doc.action_acknowledge()  # l'émetteur n'accuse pas son propre livrable
        miroir.action_acknowledge()
        self._flush()
        avant = self.Outbox.search_count([("kind", "=", "document.ack")])
        miroir.action_acknowledge()  # deuxième clic : rien de plus ne part
        self.assertEqual(self.Outbox.search_count([("kind", "=", "document.ack")]), avant)

    def test_d07_nouvelle_version_remplace_et_perime_l_accuse(self):
        doc = self._remettre(version="1.0")
        miroir = self._miroir(doc)
        miroir.action_acknowledge()
        self._flush()
        doc.invalidate_recordset()
        self.assertTrue(doc.acknowledged)

        att2 = self.env["ir.attachment"].create({"name": "politique-v2.pdf", "raw": b"PDF-2",
                                                 "mimetype": "application/pdf"})
        doc.write({"version": "2.0", "attachment_ids": [(6, 0, att2.ids)]})
        self._flush()
        doc.invalidate_recordset()
        miroir.invalidate_recordset()
        self.assertFalse(doc.acknowledged, "l'accusé porte sur un contenu, pas sur un titre")
        self.assertFalse(miroir.acknowledged)
        self.assertEqual(miroir.version, "2.0")
        self.assertEqual(len(miroir.attachment_ids), 1)
        self.assertEqual(base64.b64decode(miroir.attachment_ids.datas), b"PDF-2",
                         "le fichier de la version précédente ne traîne pas")
        notes = self.env["mail.message"].search(
            [("model", "=", "federation.document"), ("res_id", "=", miroir.id),
             ("body", "ilike", "accusé de réception est à refaire")])
        self.assertTrue(notes)

    def test_d08_un_accuse_pour_une_version_perimee_ne_vaut_pas(self):
        """Le receveur accuse pendant qu'une nouvelle version part : l'accusé ne compte pas."""
        doc = self._remettre(version="1.0")
        miroir = self._miroir(doc)
        link = self.env["federation.link"].search(
            [("res_model", "=", "federation.document"), ("res_id", "=", doc.id)], limit=1)
        doc.write({"version": "3.0"})
        self._flush()
        doc.invalidate_recordset()
        doc._federation_apply_ack(link, {"by": "Trop tard", "on": "2026-09-13 10:00:00", "version": "1.0"})
        doc.invalidate_recordset()
        self.assertFalse(doc.acknowledged, "un accusé daté d'une version périmée ne vaut pas pour celle-ci")
        notes = self.env["mail.message"].search(
            [("model", "=", "federation.document"), ("res_id", "=", doc.id),
             ("body", "ilike", "ne vaut pas pour la version en cours")])
        self.assertTrue(notes)

    def test_d09_retrait_archive_le_miroir_sans_le_supprimer(self):
        doc = self._remettre()
        miroir = self._miroir(doc)
        doc.write({"federation_peer_id": False})
        self._flush()
        miroir.invalidate_recordset()
        self.assertFalse(miroir.active, "le miroir est archivé")
        self.assertTrue(miroir.exists(), "et jamais supprimé")
        link = self.env["federation.link"].with_context(active_test=False).search(
            [("res_model", "=", "federation.document"), ("res_id", "=", doc.id)], limit=1)
        self.assertFalse(link.active, "🔴 le lien de l'émetteur s'éteint aussi, il sur-comptait avant")

    def test_d10_le_resume_recu_n_est_jamais_du_balisage(self):
        att = self.env["ir.attachment"].create({"name": "x.pdf", "raw": b"x", "mimetype": "application/pdf"})
        doc = self.env["federation.document"].create({
            "name": "<script>alert(1)</script>Rapport",
            "summary": "<b>gras</b> et <script>vol()</script>",
            "attachment_ids": [(6, 0, att.ids)],
            "peer_partner_id": self.peer_b.partner_id.id,
            "federation_peer_id": self.peer_b.id})
        self._flush()
        miroir = self._miroir(doc)
        self.assertNotIn("<script>", miroir.summary or "")
        self.assertNotIn("<b>", miroir.summary or "")
        self.assertIn("gras", miroir.summary or "")

    def test_d11_les_messages_du_chatter_traversent_aussi(self):
        """Un livrable est un objet fédéré comme un autre : sa conversation voyage."""
        doc = self._remettre()
        miroir = self._miroir(doc)
        doc.message_post(body="<p>Le chapitre 3 change la semaine prochaine.</p>",
                         message_type="comment", subtype_xmlid="mail.mt_comment")
        self._flush()
        recus = self.env["mail.message"].search(
            [("model", "=", "federation.document"), ("res_id", "=", miroir.id),
             ("body", "ilike", "chapitre 3")])
        self.assertEqual(len(recus), 1)

    def test_d12_remettre_depuis_un_enregistrement_porte_la_source(self):
        task = self.env["project.task"].create({"name": "Produire la politique", "project_id": self.project.id})
        att = self.env["ir.attachment"].create({"name": "p.pdf", "raw": b"p", "mimetype": "application/pdf"})
        doc = self.env["federation.document"].remettre(
            task, self.peer_b, titre="Politique issue de la tâche", version="1.1", attachments=att)
        self._flush()
        self.assertEqual(doc.source_ref, task)
        miroir = self._miroir(doc)
        self.assertEqual(miroir.name, "Politique issue de la tâche")
        self.assertFalse(miroir.source_ref, "la source ne traverse jamais : les identifiants se recouvrent")

    def test_d13_supprimer_deux_livrables_d_un_coup(self):
        """🔴 Le crochet de suppression cherche les liens d'un LOT : une seule
        suppression à la fois marchait, deux ensemble tombaient."""
        a = self._remettre(nom="Premier livrable")
        b = self._remettre(nom="Second livrable")
        miroir_a, miroir_b = self._miroir(a), self._miroir(b)
        (a | b).unlink()
        self._flush()
        miroir_a.invalidate_recordset()
        miroir_b.invalidate_recordset()
        self.assertFalse(miroir_a.active, "le premier miroir est archivé")
        self.assertFalse(miroir_b.active, "le second aussi")

    # --- Ce qui ne se voit qu'avec plus d'un partenaire -------------------------------
    def test_d14_le_destinataire_choisit_le_pair_et_lui_seul(self):
        """🔴 Sans destinataire, un livrable proposait TOUS les pairs actifs. Chez un
        client qui fédère avec cinq partenaires, c'est un incident de confidentialité
        en attente, pas une coquille."""
        autre = self.env["federation.peer"].create({
            "name": "Un autre partenaire", "mirror_user_id": self.admin.id, "state": "active"})
        autre._ensure_partner()
        doc = self.env["federation.document"].create({"name": "Sans destinataire"})
        self.assertFalse(doc.federation_allowed_peer_ids,
                         "sans destinataire, aucun pair n'est proposé")
        self.assertFalse(doc.federation_possible)

        doc.write({"peer_partner_id": self.peer_b.partner_id.id})
        doc.invalidate_recordset()
        self.assertEqual(doc.federation_allowed_peer_ids, self.peer_b,
                         "le destinataire désigne un seul pair")
        self.assertNotIn(autre, doc.federation_allowed_peer_ids)

    def test_d15_une_personne_de_la_maison_du_pair_suffit(self):
        """On adresse un livrable à quelqu'un, pas à une raison sociale."""
        personne = self.env["res.partner"].create({
            "name": "Quelqu'un chez le pair", "parent_id": self.peer_b.partner_id.id,
            "email": "quelquun@pair.example"})
        doc = self.env["federation.document"].create(
            {"name": "Adressé à une personne", "peer_partner_id": personne.id})
        self.assertEqual(doc.federation_allowed_peer_ids, self.peer_b)

    def test_d16_changer_le_destinataire_apres_le_partage_est_refuse(self):
        """🔴 @api.constrains ne surveille que les champs nommés. La garde du socle ne
        regarde que federation_peer_id : sans nommer le destinataire, le changer
        laissait un livrable fédéré avec un pair qui n'est plus le sien."""
        from odoo.exceptions import ValidationError
        autre = self.env["res.partner"].create({"name": "Une autre maison"})
        doc = self._remettre()
        with self.assertRaises(ValidationError):
            doc.write({"peer_partner_id": autre.id})
