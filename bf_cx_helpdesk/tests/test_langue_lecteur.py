"""Les billets nés de l'expérience client parlent la langue de leur lecteur.

La source du module est maintenant anglaise ; le français vit dans
`i18n/fr_CA.po`. Le lot a choisi un lecteur pour chaque texte écrit :

- le nom (et la description) d'un billet ouvert depuis une plainte ou un
  détracteur peut parvenir au CLIENT (accusé de réception de l'étape initiale,
  portail « Mes billets ») : sa langue, sinon celle de sa société, sinon celle de
  la société émettrice (`langue_du_client`, bf_cx_complaint.py:15-34 ;
  bf_cx_complaint.py:58-63 et bf_cx_feedback.py:52-70) ;
- la note posée au fil de la plainte reste dans la langue de qui clique
  (bf_cx_complaint.py:65-67) ;
- la note « sondage CSAT non envoyé », lue par l'équipe et parfois écrite hors
  requête, suit l'assigné du billet (helpdesk_ticket.py:41-52, 55-61).

Chaque essai fait agir une langue pour un lecteur de l'autre. Les langues sont
activées et épinglées sur chaque usager et contact créé : selon la base, en_US
est absente ou seule, et l'administrateur peut être réglé en français. Aucun
courriel ne sort : un accusé éventuel reste un `mail.mail` non envoyé.
"""
from odoo.tests import new_test_user, tagged

from odoo.addons.bf_cx.tests.common import CxBridgeCase


@tagged("post_install", "-at_install")
class TestLangue(CxBridgeCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for code in ("fr_CA", "en_CA"):
            cls.env["res.lang"]._activate_lang(code)
        cls.env["ir.module.module"]._load_module_terms(
            ["bf_cx_helpdesk"], ["fr_CA"], overwrite=True)
        cls.env = cls.env(context=dict(cls.env.context, mail_notify_force_send=False))
        cls.client_fr = cls.env["res.partner"].create(
            {"name": "Cliente Française", "email": "cliente.fr@example.com", "lang": "fr_CA"})
        cls.client_en = cls.env["res.partner"].create(
            {"name": "English Client", "email": "client.en@example.com", "lang": "en_CA"})
        cls.agent_en = new_test_user(
            cls.env, login="l9.agent.en", lang="en_CA",
            groups="base.group_user")
        # Pas de billet automatique au détracteur : chaque essai ouvre le sien.
        cls.Param.set_param("bf_cx.auto_ticket", "False")

    # ── Plainte → billet (bf_cx_complaint.py:58-67) ────────────────────
    def _plainte(self, client, objet="Retard de livraison"):
        return self.env["bf.cx.complaint"].create({
            "name": objet, "partner_id": client.id, "description": "<p>Détails</p>",
        })

    def _billet_de_plainte(self, client, lang_de_qui_clique):
        plainte = self._plainte(client)
        plainte.with_context(lang=lang_de_qui_clique).action_create_helpdesk_ticket()
        return plainte, plainte.helpdesk_ticket_id

    def _notes(self, record):
        return " ".join(str(m.body) for m in self.env["mail.message"].search([
            ("model", "=", record._name), ("res_id", "=", record.id),
        ]))

    def test_billet_de_plainte_dans_la_langue_du_client_anglais(self):
        """Agent fr_CA, client en_CA : nom du billet en anglais, note de la plainte en français."""
        plainte, billet = self._billet_de_plainte(self.client_en, "fr_CA")
        self.assertEqual(billet.name, "Complaint %s - Retard de livraison" % plainte.number)
        self.assertIn("Ticket helpdesk %s créé pour cette plainte." % billet.number,
                      self._notes(plainte))

    def test_billet_de_plainte_dans_la_langue_du_client_francais(self):
        """Agent en_CA, cliente fr_CA : nom du billet en français, note en anglais."""
        plainte, billet = self._billet_de_plainte(self.client_fr, "en_CA")
        self.assertEqual(billet.name, "Plainte %s - Retard de livraison" % plainte.number)
        self.assertIn("Helpdesk ticket %s created for this complaint." % billet.number,
                      self._notes(plainte))

    def test_billet_de_plainte_repli_sur_la_societe_du_client(self):
        """`langue_du_client` : contact sans langue, sa société en_CA décide."""
        societe = self.env["res.partner"].create(
            {"name": "English Company", "is_company": True, "lang": "en_CA"})
        contact = self.env["res.partner"].create(
            {"name": "Contact sans langue", "parent_id": societe.id, "lang": False,
             "email": "sans.langue@example.com"})
        self.assertFalse(contact.lang)
        plainte, billet = self._billet_de_plainte(contact, "fr_CA")
        self.assertEqual(billet.name, "Complaint %s - Retard de livraison" % plainte.number)

    # ── Détracteur → billet de suivi (bf_cx_feedback.py:52-70) ─────────
    def _billet_de_detracteur(self, client, lang_du_cadre):
        retour = self.env["bf.cx.feedback"].create({
            "partner_id": client.id if client else False, "kind": "nps", "score": 2,
            "score_max": 10, "source": "manual",
        })
        return retour.with_context(lang=lang_du_cadre)._create_followup_ticket()

    def test_billet_de_detracteur_client_anglais(self):
        billet = self._billet_de_detracteur(self.client_en, "fr_CA")
        self.assertEqual(billet.name, "Detractor follow-up - English Client")
        self.assertIn("Rating of 2.0/10 received from English Client.", billet.description)

    def test_billet_de_detracteur_cliente_francaise(self):
        billet = self._billet_de_detracteur(self.client_fr, "en_CA")
        self.assertEqual(billet.name, "Suivi détracteur - Cliente Française")
        self.assertIn("Note de 2.0/10 reçue de Cliente Française.", billet.description)

    def test_billet_de_detracteur_anonyme_dans_la_langue_de_la_societe(self):
        """Sans répondant : la langue de la société émettrice, « un répondant anonyme »."""
        self.env.company.partner_id.lang = "en_CA"
        billet = self._billet_de_detracteur(False, "fr_CA")
        self.assertTrue(billet.name.startswith("Detractor follow-up - "), billet.name)
        self.assertIn("received from an anonymous respondent.", billet.description)

    # ── Note CSAT non envoyé (helpdesk_ticket.py:41-52) ────────────────
    def test_note_csat_bloque_dans_la_langue_de_l_assigne(self):
        """Fermeture lancée en fr_CA, billet assigné à un agent en_CA : note en anglais."""
        if "helpdesk.ticket.csat" not in self.env:
            self.skipTest("bf_helpdesk (sondage natif) absent")
        self.set_gate("bf_cx.guard_rating_requests", True)
        self.env["mail.blacklist"].create({"email": self.client_fr.email})
        billet = self.env["helpdesk.ticket"].create({
            "name": "Billet CSAT", "description": "<p>x</p>",
            "partner_id": self.client_fr.id, "user_id": self.agent_en.id,
        })
        self.assertIsNone(billet.with_context(lang="fr_CA")._send_csat_invite())
        notes = self._notes(billet)
        self.assertIn("CSAT survey not sent to Cliente Française", notes)
        self.assertNotIn("Sondage CSAT non envoyé", notes)

    def test_note_csat_bloque_sans_assigne_dans_la_langue_du_cadre(self):
        """helpdesk_ticket.py:55-61 : sans assigné, la langue de l'environnement."""
        if "helpdesk.ticket.csat" not in self.env:
            self.skipTest("bf_helpdesk (sondage natif) absent")
        self.set_gate("bf_cx.guard_rating_requests", True)
        self.env["mail.blacklist"].create({"email": self.client_en.email})
        billet = self.env["helpdesk.ticket"].create({
            "name": "Billet CSAT sans assigné", "description": "<p>x</p>",
            "partner_id": self.client_en.id, "user_id": False,
        })
        billet.with_context(lang="fr_CA")._send_csat_invite()
        self.assertIn("Sondage CSAT non envoyé à English Client", self._notes(billet))
