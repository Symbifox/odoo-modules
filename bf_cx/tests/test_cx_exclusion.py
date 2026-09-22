"""L'exclusion de compte : « ni lui, ni aucun autre employé ».

Ce qui rend cette promesse difficile n'est pas de bloquer une personne —
c'est qu'elle doit tenir pour quelqu'un qui n'existe pas encore, et sur
des chemins sortants qui ne se ressemblent pas : la vague, le rappel
collectif, le renvoi ciblé, et le crochet central des demandes
d'évaluation. Une exclusion qui fuit par UN de ces chemins n'est pas une
exclusion, et c'est exactement ce qu'aucun tableau de bord ne montre.

Le contre-exemple compte autant : l'exclusion ne doit PAS toucher l'envoi
d'essai, sinon on ne peut plus éprouver un compte qu'on vient d'exclure.
"""
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged

SEND = "odoo.addons.mail.models.mail_mail.MailMail.send"


def _delivered(mail_self, *args, **kwargs):
    mail_self.write({"state": "sent"})


def sending():
    return patch(SEND, autospec=True, side_effect=_delivered)


@tagged("post_install", "-at_install")
class TestCxExclusion(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Param = cls.env["ir.config_parameter"].sudo()
        cls.Param.set_param("bf_cx.solicitation_cooldown_days", "0")
        cls.program = cls.env.ref("bf_cx.program_nps_default")
        P = cls.env["res.partner"]
        cls.societe = P.create({
            "name": "Compte Écarté inc.", "email": "info@ecarte.example.com",
            "is_company": True})
        cls.employe = P.create({
            "name": "Employé Écarté", "email": "employe@ecarte.example.com",
            "parent_id": cls.societe.id})
        cls.autre_societe = P.create({
            "name": "Compte Gardé inc.", "email": "info@garde.example.com",
            "is_company": True})
        cls.voisin = P.create({
            "name": "Employé Gardé", "email": "employe@garde.example.com",
            "parent_id": cls.autre_societe.id})

    def _wave(self, partners, **vals):
        return self.env["bf.cx.wave"].create(dict({
            "name": "Vague exclusion",
            "program_id": self.program.id,
            "partner_ids": [(6, 0, partners.ids)],
            "deadline": fields.Datetime.now() + timedelta(days=7),
        }, **vals))

    # ── La portée : la société couvre ses gens, présents et à venir ───────

    def test_excluding_the_company_excludes_its_contacts(self):
        self.societe.bf_cx_exclude = True
        self.assertTrue(self.employe._bf_cx_is_excluded())
        self.assertTrue(self.employe.bf_cx_exclude_effective)
        self.assertFalse(self.employe.bf_cx_exclude,
                         "le drapeau en propre reste faux : c'est la société")

    def test_a_contact_created_later_is_covered(self):
        """La moitié de la promesse : « ni aucun AUTRE employé »."""
        self.societe.bf_cx_exclude = True
        tardif = self.env["res.partner"].create({
            "name": "Recrue de l'an prochain",
            "email": "recrue@ecarte.example.com",
            "parent_id": self.societe.id,
        })
        self.assertTrue(tardif._bf_cx_is_excluded())
        allowed, blocked = tardif._bf_cx_split_solicitable()
        self.assertFalse(allowed)
        self.assertEqual(blocked, tardif)

    def test_excluding_one_person_leaves_the_siblings_alone(self):
        self.employe.bf_cx_exclude = True
        collegue = self.env["res.partner"].create({
            "name": "Collègue", "email": "collegue@ecarte.example.com",
            "parent_id": self.societe.id})
        self.assertTrue(self.employe._bf_cx_is_excluded())
        self.assertFalse(collegue._bf_cx_is_excluded())

    def test_another_account_is_untouched(self):
        self.societe.bf_cx_exclude = True
        allowed, blocked = (self.voisin | self.employe)._bf_cx_split_solicitable()
        self.assertEqual(allowed, self.voisin)
        self.assertEqual(blocked, self.employe)

    # ── Les quatre chemins sortants ──────────────────────────────────────

    def test_a_wave_does_not_invite_an_excluded_contact(self):
        self.societe.bf_cx_exclude = True
        wave = self._wave(self.employe | self.voisin)
        with sending():
            wave.action_send()
        self.assertEqual(wave.user_input_ids.partner_id, self.voisin)

    def test_the_collective_reminder_skips_an_excluded_contact(self):
        """Le chemin qui fuit : `action_remind` ne passe pas par le garde-fou.

        Le cas qui compte est l'exclusion posée APRÈS le départ de la vague.
        """
        wave = self._wave(self.employe | self.voisin)
        with sending():
            wave.action_send()
        self.assertEqual(len(wave.user_input_ids), 2)
        self.societe.bf_cx_exclude = True          # après l'envoi
        before = self.env["mail.mail"].search_count([])
        with sending():
            wave.action_remind()
        created = self.env["mail.mail"].search_count([]) - before
        self.assertEqual(created, 1, "seul le compte gardé est relancé")
        last = self.env["mail.mail"].search(
            [("model", "=", "survey.user_input")], order="id desc", limit=1)
        self.assertEqual(last.recipient_ids, self.voisin)

    def test_the_targeted_resend_refuses_an_excluded_contact(self):
        wave = self._wave(self.employe | self.voisin)
        with sending():
            wave.action_send()
        answer = wave.user_input_ids.filtered(
            lambda i: i.partner_id == self.employe)
        self.societe.bf_cx_exclude = True
        with self.assertRaises(UserError):
            answer.action_bf_cx_resend_invite()

    def test_the_central_rating_hook_refuses_an_excluded_contact(self):
        """Couvre d'un coup les notes de projet et le CSAT des billets."""
        self.societe.bf_cx_exclude = True
        allowed, blocked = self.employe._bf_cx_split_solicitable()
        self.assertFalse(allowed)
        self.assertEqual(blocked, self.employe)

    # ── L'exclusion prime sur tout, sauf sur l'essai ──────────────────────

    def test_exclusion_survives_a_zeroed_cooldown(self):
        """Le renvoi neutralise la cadence (`days=0`) : l'exclusion, non.

        Sans cette distinction, le bouton « Renvoyer » serait la porte
        dérobée de l'exclusion.
        """
        self.societe.bf_cx_exclude = True
        allowed, blocked = self.employe._bf_cx_split_solicitable(days=0)
        self.assertFalse(allowed)
        self.assertEqual(blocked, self.employe)

    def test_the_test_send_is_not_blocked_by_an_exclusion(self):
        """Le contre-exemple : l'essai doit rester jouable, toujours.

        Il est délibérément hors de TOUS les garde-fous ; l'y soumettre
        rendrait impossible d'éprouver un compte qu'on vient d'exclure.
        """
        self.Param.set_param("bf_cx.test_partner_id", str(self.employe.id))
        self.societe.bf_cx_exclude = True
        wave = self._wave(self.voisin)
        with sending():
            wave.action_send_test()
        self.assertEqual(wave.user_input_ids.filtered("test_entry").partner_id,
                         self.employe)

    # ── Le champ doit se CHERCHER, pas seulement se lire ─────────────────

    def test_the_effective_flag_is_searchable_and_tells_the_truth(self):
        """Le défaut que la lecture par enregistrement ne montre jamais.

        Un calculé non stocké sans `search=` n'est pas refusé par l'ORM :
        le critère est écarté du domaine, et la recherche rend TOUT : `= True`
        et `= False` rendent alors le même ensemble. La dernière assertion
        est celle qui mord —
        deux recherches contraires ne peuvent pas partager un seul
        enregistrement.
        """
        self.societe.bf_cx_exclude = True
        Partner = self.env["res.partner"]
        exclus = Partner.search([("bf_cx_exclude_effective", "=", True)])
        gardes = Partner.search([("bf_cx_exclude_effective", "=", False)])
        self.assertIn(self.societe, exclus)
        self.assertIn(self.employe, exclus, "couvert par sa société")
        self.assertNotIn(self.voisin, exclus)
        self.assertIn(self.voisin, gardes)
        self.assertNotIn(self.employe, gardes)
        self.assertFalse(
            exclus & gardes,
            "deux recherches contraires ne peuvent pas se recouper : "
            "si elles le font, le critère est écarté en silence",
        )

    def test_the_negated_search_is_the_complement(self):
        self.societe.bf_cx_exclude = True
        Partner = self.env["res.partner"]
        self.assertEqual(
            Partner.search([("bf_cx_exclude_effective", "!=", True)]),
            Partner.search([("bf_cx_exclude_effective", "=", False)]),
        )

    # ── Traçabilité de la décision ───────────────────────────────────────

    def test_the_decision_is_dated_and_signed(self):
        self.assertFalse(self.societe.bf_cx_exclude_date)
        self.societe.write({"bf_cx_exclude": True,
                            "bf_cx_exclude_reason": "Motif d'essai"})
        self.assertTrue(self.societe.bf_cx_exclude_date)
        self.assertEqual(self.societe.bf_cx_exclude_user_id, self.env.user)

    def test_lifting_the_exclusion_clears_its_trace(self):
        self.societe.bf_cx_exclude = True
        self.societe.bf_cx_exclude = False
        self.assertFalse(self.societe.bf_cx_exclude_date)
        self.assertFalse(self.societe.bf_cx_exclude_user_id)
        self.assertFalse(self.employe._bf_cx_is_excluded())

    # ── La porte d'entrée existe dans l'interface ────────────────────────

    def test_the_flag_is_on_the_partner_form(self):
        """Vue lue par un OPÉRATEUR CX, pas par le superutilisateur.

        La page porte `groups="bf_cx.group_bf_cx_user"` : `get_view`
        retire les noeuds réservés à un groupe que le lecteur n'a pas, et
        le superutilisateur des essais n'est pas dans celui-ci. Lire l'arch
        en son nom mesurerait donc autre chose que ce qu'on veut établir.
        """
        operateur = self.env["res.users"].create({
            "name": "Opératrice CX", "login": "operatrice-cx@essai.invalid",
            "groups_id": [(6, 0, [
                self.env.ref("base.group_user").id,
                self.env.ref("bf_cx.group_bf_cx_user").id,
            ])],
        })
        arch = self.env["res.partner"].with_user(operateur).get_view(
            self.env.ref("base.view_partner_form").id, "form")["arch"]
        self.assertIn("bf_cx_exclude", arch)
        self.assertIn("bf_cx_exclude_reason", arch)

    def test_the_flag_is_hidden_from_a_user_without_cx_rights(self):
        """L'autre moitié : la page ne s'affiche pas à qui n'a rien à y faire."""
        quidam = self.env["res.users"].create({
            "name": "Quidam", "login": "quidam@essai.invalid",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id])],
        })
        arch = self.env["res.partner"].with_user(quidam).get_view(
            self.env.ref("base.view_partner_form").id, "form")["arch"]
        self.assertNotIn("bf_cx_exclude", arch)

    # ── La décision appartient à l'Expérience client ─────────────────────

    def test_a_user_without_cx_rights_cannot_exclude_or_lift(self):
        """La page est masquée, mais un champ s'écrit aussi par `call_kw`."""
        quidam = self.env["res.users"].create({
            "name": "Sans Droit CX", "login": "sans-droit-cx@essai.invalid",
            "groups_id": [(6, 0, [
                self.env.ref("base.group_user").id,
                self.env.ref("base.group_partner_manager").id,
            ])],
        })
        with self.assertRaises(AccessError):
            self.societe.with_user(quidam).write({"bf_cx_exclude": True})
        with self.assertRaises(AccessError):
            self.env["res.partner"].with_user(quidam).create(
                {"name": "Compte Neuf", "bf_cx_exclude": True})
        # Le reste de la fiche, lui, reste modifiable.
        self.societe.with_user(quidam).write({"comment": "Note"})

    def test_the_trace_cannot_be_forged_by_the_client(self):
        """Ni antidatée, ni attribuée à quelqu'un d'autre."""
        autre = self.env["res.users"].create({
            "name": "Autre Personne", "login": "autre-personne@essai.invalid",
        })
        self.societe.write({
            "bf_cx_exclude": True,
            "bf_cx_exclude_date": "2001-01-01 00:00:00",
            "bf_cx_exclude_user_id": autre.id,
        })
        self.assertEqual(self.societe.bf_cx_exclude_user_id, self.env.user)
        self.assertGreater(self.societe.bf_cx_exclude_date.year, 2001)

    def test_an_exclusion_set_at_creation_is_dated_and_signed(self):
        compte = self.env["res.partner"].create(
            {"name": "Compte Créé Exclu", "bf_cx_exclude": True})
        self.assertTrue(compte.bf_cx_exclude_date)
        self.assertEqual(compte.bf_cx_exclude_user_id, self.env.user)
