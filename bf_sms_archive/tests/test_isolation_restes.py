"""Isolation entre personnes, côté SMS : ce qu'un lot de sécurité avait laissé ouvert.

1. Le contenu d'un SMS archivé ne se réécrit pas hors superutilisateur. La règle
   d'écriture ouvre le message aux co-usagers de la ligne partagée, pour le
   marquer lu : B réécrivait le corps d'un SMS de A.
2. Une pièce MMS ne s'ajoute qu'à ses propres messages.
3. L'endpoint d'un abonnement de poussée ne suffit pas à le reprendre au nom
   d'un autre usager : il faut aussi ses clés.
4. L'empreinte de dédoublonnage vaut par fil : le même SMS reçu par deux
   personnes n'est plus avalé chez la deuxième.

Chaque refus est doublé du geste légitime qui doit rester permis.
Données inventées.
"""

from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("bf_sms_archive", "post_install", "-at_install")
class TestIsolationRestes(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        g = "bf_sms_archive.group_sms_user"
        cls.owner = new_test_user(cls.env, login="sms_restes_a", groups=g)
        cls.mate = new_test_user(cls.env, login="sms_restes_b", groups=g)
        cls.line = cls.env["sms.archive.line"].create({
            "label": "Ligne partagée d'essai", "did": "5145550281",
            "owner_id": cls.owner.id, "user_ids": [(6, 0, cls.mate.ids)],
        })
        cls.thread = cls.env["sms.archive.thread"].create({
            "phone_normalized": "+15145550282", "owner_id": cls.owner.id,
            "line_id": cls.line.id,
        })
        cls.msg = cls.env["sms.archive.message"].create({
            "thread_id": cls.thread.id, "message_hash": "h-essai-a",
            "direction": "in", "body": "Le code est 4321",
            "date_sent": "2026-10-03 07:00:00", "line_id": cls.line.id, "is_read": False,
        })

    # ── 1. Contenu figé ───────────────────────────────────────────

    def test_co_usager_ne_reecrit_pas_le_corps(self):
        self.assertTrue(self.msg.with_user(self.mate).read(["body"]), "le co-usager lit")
        with self.assertRaises(AccessError):
            self.msg.with_user(self.mate).write({"body": "Le code est 0000"})
        self.assertEqual(self.msg.body, "Le code est 4321")

    def test_proprietaire_ne_reecrit_pas_le_corps_non_plus(self):
        with self.assertRaises(AccessError):
            self.msg.with_user(self.owner).write({"direction": "out"})

    def test_marquer_lu_reste_permis(self):
        self.msg.with_user(self.mate).write({"is_read": True})
        self.assertTrue(self.msg.is_read)

    def test_ingestion_ecrit_toujours(self):
        self.msg.sudo().write({"delivery_state": "sent"})
        self.assertEqual(self.msg.delivery_state, "sent")

    def test_co_usager_ne_forge_pas_un_sms_dans_le_fil_de_a(self):
        """La règle d'écriture du fil s'ouvre au co-usager ;
        il y créait un SMS « reçu » entier, antidaté."""
        forge = {"thread_id": self.thread.id, "line_id": self.line.id, "direction": "in",
                 "body": "Virement reçu, code 0000", "date_sent": "2026-10-01 09:00:00",
                 "message_hash": "h-essai-forge"}
        with self.assertRaises(AccessError):
            with self.env.cr.savepoint():
                self.env["sms.archive.message"].with_user(self.mate).create(dict(forge))
        with self.assertRaises(AccessError):
            with self.env.cr.savepoint():
                self.thread.with_user(self.mate).write({"message_ids": [(0, 0, dict(forge))]})
        self.assertFalse(self.env["sms.archive.message"].search(
            [("message_hash", "=", "h-essai-forge")]))

    def test_proprietaire_cree_dans_son_fil(self):
        msg = self.env["sms.archive.message"].with_user(self.owner).create({
            "thread_id": self.thread.id, "direction": "out", "body": "import",
            "date_sent": "2026-10-01 09:00:00", "message_hash": "h-essai-import"})
        self.assertEqual(msg.owner_id, self.owner)

    # ── 2. Pièces MMS ─────────────────────────────────────────────

    def test_co_usager_n_ajoute_pas_de_piece(self):
        with self.assertRaises(AccessError):
            self.env["sms.archive.mms.part"].with_user(self.mate).create({
                "message_id": self.msg.id, "content_type": "text/plain",
                "text_content": "ajout de B"})

    def test_co_usager_ne_modifie_pas_une_piece(self):
        part = self.env["sms.archive.mms.part"].create({
            "message_id": self.msg.id, "content_type": "text/plain", "text_content": "A"})
        with self.assertRaises(AccessError):
            part.with_user(self.mate).write({"text_content": "B"})

    def test_proprietaire_ajoute_a_ses_messages(self):
        part = self.env["sms.archive.mms.part"].with_user(self.owner).create({
            "message_id": self.msg.id, "content_type": "text/plain", "text_content": "A"})
        self.assertEqual(part.owner_id, self.owner)

    # ── 3. Poussée ────────────────────────────────────────────────

    def _abonner(self, user, endpoint, p256dh, auth):
        return self.env["sms.archive.thread"].with_user(user).push_subscribe(
            endpoint, p256dh, auth)

    def _abonnement(self, endpoint):
        return self.env["sms.archive.push.subscription"].sudo().with_context(
            active_test=False).search([("endpoint", "=", endpoint)])

    def test_endpoint_d_autrui_sans_ses_cles_refuse(self):
        ep = "https://push.example.test/essai-a"
        self.assertTrue(self._abonner(self.owner, ep, "cle-a", "auth-a"))
        self.assertFalse(self._abonner(self.mate, ep, "cle-b", "auth-b"),
                         "le refus doit se dire au client")
        sub = self._abonnement(ep)
        self.assertEqual(len(sub), 1)
        self.assertEqual(sub.user_id, self.owner, "B a repris l'abonnement de A")
        self.assertEqual((sub.p256dh, sub.auth), ("cle-a", "auth-a"))

    def test_navigateur_partage_change_de_session(self):
        ep = "https://push.example.test/essai-poste"
        self._abonner(self.owner, ep, "cle-poste", "auth-poste")
        self._abonner(self.mate, ep, "cle-poste", "auth-poste")
        self.assertEqual(self._abonnement(ep).user_id, self.mate)

    def test_abonnement_desactive_se_reactive(self):
        ep = "https://push.example.test/essai-retour"
        self._abonner(self.owner, ep, "cle-a", "auth-a")
        self.env["sms.archive.thread"].with_user(self.owner).push_unsubscribe(ep)
        self.assertFalse(self._abonnement(ep).active)
        self._abonner(self.owner, ep, "cle-a2", "auth-a2")
        sub = self._abonnement(ep)
        self.assertEqual(len(sub), 1)
        self.assertTrue(sub.active)
        self.assertEqual(sub.p256dh, "cle-a2")

    # ── 4. Empreinte par fil ──────────────────────────────────────

    def _ingerer(self, owner):
        return self.env["sms.archive.message"]._ingest_one(
            phone_raw="+15145550299", owner_id=owner.id, direction="in",
            body="Même texte aux deux", date_ms=1791025200000)

    def test_meme_sms_chez_deux_personnes(self):
        msg_a, cree_a = self._ingerer(self.owner)
        msg_b, cree_b = self._ingerer(self.mate)
        self.assertTrue(cree_a)
        self.assertTrue(cree_b, "le SMS de B a été avalé")
        self.assertNotEqual(msg_a, msg_b)
        self.assertEqual(msg_b.owner_id, self.mate)
        self.assertEqual(msg_a.message_hash, msg_b.message_hash)

    def test_doublon_rejoue_rend_le_sien(self):
        msg_a, _cree = self._ingerer(self.owner)
        self._ingerer(self.mate)
        msg_a.thread_id.active = False
        rejoue, cree = self._ingerer(self.owner)
        self.assertFalse(cree)
        self.assertEqual(rejoue, msg_a)
        self.assertFalse(msg_a.thread_id.active, "un doublon rejoué a désarchivé le fil")
