from contextlib import contextmanager
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("bf_helpdesk", "bf_helpdesk_triage")
class TestTicketTriage(TransactionCase):
    """Triage IA par le pont (bf_ai_bridge → /helpdesk/triage)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.Ticket = cls.env["helpdesk.ticket"]
        cls.IConf = cls.env["ir.config_parameter"].sudo()
        cls.alias = cls.env["mail.alias"].create({
            "alias_name": "tr-test",
            "alias_model_id": cls.env.ref("helpdesk_mgmt.model_helpdesk_ticket").id,
        })
        cls.membre = cls.env["res.users"].create({
            "name": "Jane Doe",
            "login": "jdoe.triage@example.test",
        })
        cls.team = cls.env["helpdesk.ticket.team"].create({
            "name": "Triage Test Team",
            "alias_id": cls.alias.id,
            "user_ids": [(6, 0, cls.membre.ids)],
        })

    def _ticket(self, **extra):
        valeurs = {
            "name": "Imprimante en panne au poste 3",
            "description": "<p>Bonjour, mon imprimante refuse de démarrer.</p>",
            "team_id": self.team.id,
        }
        valeurs.update(extra)
        return self.Ticket.create(valeurs)

    @contextmanager
    def _patch_pont(self, call):
        """Répondre à la place du pont, socket réputée présente.

        On corrige la classe du REGISTRE, pas celle du fichier : Odoo compose
        une classe neuve par modèle au chargement, et un patch posé sur la
        classe déclarée ne serait jamais celui qu'appelle ``env[...]``.
        """
        Pont = type(self.env["bf.ai.bridge"])
        with patch.object(Pont, "available", lambda *a, **k: True), \
                patch.object(Pont, "call", call):
            yield

    # ── état de départ ────────────────────────────────────────────────

    def test_default_state_none(self):
        ticket = self._ticket()
        self.assertEqual(ticket.triage_state, "none")
        self.assertFalse(ticket.triage_suggestion_html)
        self.assertFalse(ticket.triage_last_run)

    # ── configuration : la socket manque ──────────────────────────────

    def test_socket_absente_leve_et_ne_touche_pas_au_billet(self):
        ticket = self._ticket()
        with patch.object(
            type(self.env["bf.ai.bridge"]), "available", lambda *a, **k: False,
        ):
            with self.assertRaises(UserError):
                ticket.action_triage_with_claude()
        self.assertEqual(ticket.triage_state, "none")
        self.assertFalse(ticket.triage_last_run)

    # ── cas nominal ───────────────────────────────────────────────────

    def test_triage_reussi_stocke_la_suggestion(self):
        ticket = self._ticket()
        reponse = {"data": {
            "categorisation": "Panne matérielle sur un poste de travail.",
            "stage": None,
            "stage_motif": "Le billet arrive, aucun stage plus avancé ne convient.",
            "assignation": "Jane Doe",
            "assignation_motif": "Seule membre de l'équipe.",
            "reponse": "Nous avons bien reçu votre demande. Nous regardons l'imprimante.",
            "confiance": 82,
        }}
        with self._patch_pont(lambda *a, **k: reponse):
            ticket.action_triage_with_claude()
        self.assertEqual(ticket.triage_state, "done")
        self.assertTrue(ticket.triage_last_run)
        html = ticket.triage_suggestion_html
        self.assertIn("Panne matérielle", html)
        self.assertIn("Jane Doe", html)
        self.assertIn("82", html)

    def test_le_billet_part_avec_ses_stages_et_ses_membres(self):
        ticket = self._ticket()
        vu = {}

        def _call(self_, endpoint, payload, timeout=100, headers=None):
            vu["endpoint"] = endpoint
            vu["payload"] = payload
            return {"data": {"categorisation": "x"}}

        with self._patch_pont(_call):
            ticket.action_triage_with_claude()
        self.assertEqual(vu["endpoint"], "/helpdesk/triage")
        charge = vu["payload"]
        self.assertEqual(charge["sujet"], "Imprimante en panne au poste 3")
        self.assertEqual(charge["equipe"], "Triage Test Team")
        self.assertIn("Jane Doe", charge["membres"])
        # La description part en texte : le modèle n'a pas à lire du HTML.
        self.assertIn("imprimante refuse", charge["description"])
        self.assertNotIn("<p>", charge["description"])

    # ── ce que le pont rend n'est pas cru sur parole ──────────────────

    def test_html_monte_par_odoo_et_echappe(self):
        ticket = self._ticket()
        reponse = {"data": {
            "categorisation": "<script>alert(1)</script> injection",
            "reponse": "Bonjour & merci",
        }}
        with self._patch_pont(lambda *a, **k: reponse):
            ticket.action_triage_with_claude()
        html = ticket.triage_suggestion_html
        self.assertNotIn("<script>", html)
        self.assertIn("&amp;", html)
        self.assertEqual(ticket.triage_state, "done")

    # ── échecs : ils se déposent, ils ne lèvent pas ───────────────────

    def test_erreur_rendue_par_le_pont_marque_erreur(self):
        ticket = self._ticket()
        with self._patch_pont(lambda *a, **k: {"error": "Le triage a dépassé le délai de 90 s."}):
            ticket.action_triage_with_claude()
        self.assertEqual(ticket.triage_state, "error")
        self.assertIn("délai", ticket.triage_suggestion_html)
        self.assertTrue(ticket.triage_last_run)

    def test_transport_qui_casse_marque_erreur(self):
        ticket = self._ticket()

        def _boom(*a, **k):
            raise ConnectionRefusedError("socket fermée")

        with self._patch_pont(_boom):
            ticket.action_triage_with_claude()
        self.assertEqual(ticket.triage_state, "error")
        self.assertIn("socket fermée", ticket.triage_suggestion_html)

    def test_reponse_vide_marque_erreur(self):
        ticket = self._ticket()
        with self._patch_pont(lambda *a, **k: {}):
            ticket.action_triage_with_claude()
        self.assertEqual(ticket.triage_state, "error")
