from lxml import etree

from odoo import Command
from odoo.tests import tagged

from .common import BfBudgetAnalyticCommon


@tagged("post_install", "-at_install")
class TestAnalyticUnfoldArch(BfBudgetAnalyticCommon):
    """Les DEUX boutons de dépliage sont gardés par des groupes différents.

    🔴 Échanger leurs `groups` recrée une impasse d'écran que ce module
    ferme : le groupe feuilles de temps n'a AUCUN droit sur le grand livre, et
    le dépliage d'une ligne comptable restreinte en EXIGE un (son domaine
    traverse `move_line_id.…`, donc son évaluation sous-cherche
    `account.move.line`). Un porteur du seul groupe feuilles de temps verrait
    un bouton toujours refusé. Mesuré en base : `account.group_account_readonly`
    n'implique pas `analytic.group_analytic_accounting`, et réciproquement —
    les deux attributions sont bien disjointes, l'essai le fige.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Users = cls.env["res.users"].with_context(no_reset_password=True)
        cls.user_ledger = Users.create(
            {
                "name": "Lecteur du grand livre",
                "login": "grandlivre.budget@essai.invalid",
                "company_id": cls.company.id,
                "company_ids": [Command.set([cls.company.id])],
                "groups_id": [
                    Command.set(
                        [
                            cls.env.ref("base.group_user").id,
                            cls.env.ref("bf_budget.group_bf_budget_user").id,
                            cls.env.ref("account.group_account_readonly").id,
                        ]
                    )
                ],
            }
        )

    def _unfold_buttons(self, user):
        """Les modificateurs `invisible` des boutons de dépliage SERVIS au rôle.

        Le post-traitement des vues RETIRE les nœuds dont l'usager n'a pas les
        groupes : ce qui reste dans l'arch est ce que la personne peut cliquer.
        """
        views = (
            self.env["bf.budget.line"].with_user(user).get_views([(False, "form")])
        )
        arch = etree.fromstring(views["views"]["form"]["arch"])
        return [
            node.get("invisible") or ""
            for node in arch.xpath("//button[@name='action_view_analytic_lines']")
        ]

    def test_the_timesheet_role_only_gets_the_internal_cost_unfold(self):
        """Anne porte budget + feuilles de temps : elle a le bouton du COÛT
        INTERNE (celui que son groupe peut réellement déplier), et pas celui de
        la ligne comptable restreinte."""
        buttons = self._unfold_buttons(self.user_anne)
        self.assertEqual(len(buttons), 1)
        self.assertIn("!=", buttons[0])
        self.assertIn("internal_cost", buttons[0])

    def test_the_ledger_role_only_gets_the_narrowed_accounting_unfold(self):
        """Un lecteur du grand livre a le bouton de la ligne COMPTABLE
        restreinte, et pas celui du coût interne : son groupe ne donne aucun
        droit sur les feuilles de temps."""
        buttons = self._unfold_buttons(self.user_ledger)
        self.assertEqual(len(buttons), 1)
        self.assertIn("==", buttons[0])
        self.assertIn("internal_cost", buttons[0])

    def test_a_bare_budget_reader_gets_no_unfold_button_at_all(self):
        """Le lecteur de budget sans aucun droit analytique garde les sommes et
        ne voit aucun bouton : le dépliage lui refuserait de toute façon."""
        self.assertEqual(self._unfold_buttons(self.user_bare), [])
        arch = (
            self.env["bf.budget.line"]
            .with_user(self.user_bare)
            .get_views([(False, "form")])["views"]["form"]["arch"]
        )
        # Les CHIFFRES, eux, restent servis : c'est le détail qui est fermé.
        self.assertIn("analytic_line_count", arch)
        self.assertIn("timesheet_hours", arch)
