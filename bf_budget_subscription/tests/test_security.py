from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import new_test_user, tagged

from .common import BfBudgetSubscriptionCommon


@tagged("post_install", "-at_install")
class TestSubscriptionBudgetSecurity(BfBudgetSubscriptionCommon):
    """Le registre ne s'ouvre qu'à qui peut le lire — et à qui ça refuse.

    ⚠️ Sous de VRAIS comptes, jamais uid 1, et sans lecture sudo avant : le
    cache de l'ORM est par transaction, un montant lu d'abord en sudo serait
    resservi tel quel au compte sans droits et l'essai passerait sans rien
    prouver.

    🔴 MESURÉ EN BASE, ET C'EST CE QUI DONNE SA FORME À CET ESSAI :
    `base.group_user` IMPLIQUE `bf_subscription.group_subscription_user`
    (`res_groups_implied_rel`). Sur une base de série, AUCUN usager interne
    n'est donc refusé par ces gardes : l'écran d'une ligne budgétaire
    n'explose pour personne aujourd'hui, et le garde ne retire rien à
    personne. Le rôle refusé se construit À LA MAIN — un porteur du seul
    `bf_budget.group_bf_budget_user`, sans type d'usager (la contrainte du
    cœur `_check_one_user_type` n'en interdit qu'un DEUXIÈME, pas l'absence).
    Ce rôle-là existe pour une raison : le jour où `bf_subscription` resserre
    l'accès à son registre — il nomme des fournisseurs et des montants — ces
    deux gardes sont déjà en place, et c'est ce que cet essai fige.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Le témoin POSITIF : le lecteur de budget ORDINAIRE, qui porte le
        # registre par l'implication du cœur. Rien ne doit lui être retiré.
        cls.reader = new_test_user(
            cls.env,
            login="budget_subscription_reader",
            groups="base.group_user,bf_budget.group_bf_budget_user",
            company_id=cls.company.id,
            company_ids=[Command.set([cls.company.id])],
        )
        assert cls.reader._has_group("bf_subscription.group_subscription_user"), (
            "l'implication base.group_user → group_subscription_user a changé : "
            "relire la docstring de cette classe avant de toucher aux essais"
        )
        # Le rôle que les gardes refusent : budget, et rien d'autre.
        cls.no_register = new_test_user(
            cls.env,
            login="budget_sans_registre",
            groups="bf_budget.group_bf_budget_user",
            company_id=cls.company.id,
            company_ids=[Command.set([cls.company.id])],
        )
        assert not cls.no_register._has_group(
            "bf_subscription.group_subscription_user"
        )

    def _budgeted_line(self):
        """Une ligne nue sur un poste qui porte un abonnement mensuel."""
        subscription = self._make_subscription("monthly", 100.0)
        subscription.budget_position_id = self.position_software
        budget = self._make_budget(planned=1200.0)
        budget.line_ids.invalidate_recordset()
        return budget.line_ids

    def test_an_ordinary_budget_reader_loses_nothing(self):
        """Témoin positif : le garde refuse un rôle, pas tous les rôles. Un
        lecteur de budget de série lit les sommes, ouvre le registre, et son
        écran porte les tags."""
        line = self._budgeted_line()
        self.env.invalidate_all()
        as_reader = line.with_user(self.reader)
        self.assertEqual(as_reader.subscription_count, 1)
        self.assertEqual(as_reader.subscription_period_total, 1200.0)
        action = as_reader.action_view_subscriptions()
        self.assertEqual(action["res_model"], "subscription.subscription")
        arch = (
            self.env["bf.budget.line"]
            .with_user(self.reader)
            .get_views([(False, "form")])["views"]["form"]["arch"]
        )
        self.assertIn("subscription_ids", arch)

    def test_the_counts_stay_readable_without_the_register(self):
        """🔴 Le chiffre est stable par rôle, c'est le DÉTAIL qui est fermé.

        Les compteurs se calculent en sudo : le rôle refusé lit le même
        nombre d'abonnements que tout le monde, sinon un poste sous contrat
        aurait l'air d'un poste sans engagement. C'est aussi ce qui prouve que
        l'essai suivant mesure un garde de CHAMP et non un refus de modèle :
        cette ligne-là, ce rôle la lit.

        ⚠️ Pas les MONETARY ici, et ce n'est pas un manque du module : l'ACL
        de lecture de `res.currency` vient de `base.group_user`, que ce rôle
        construit à la main n'a pas, et l'arrondi d'un Monetary la demande.
        La stabilité des MONTANTS par rôle est éprouvée sur le lecteur de
        budget ordinaire (essai ci-dessus) et, pour le réalisé, au socle.
        """
        line = self._budgeted_line()
        self.env.invalidate_all()
        as_role = line.with_user(self.no_register)
        self.assertEqual(as_role.subscription_count, 1)
        self.assertEqual(as_role.position_subscription_count, 1)

    def test_the_register_stays_behind_subscription_rights(self):
        """Le bouton caché ne protège que l'écran : la méthode, appelable par
        RPC, rendrait la liste des ids d'abonnements du poste. Elle refuse
        d'abord à qui ne peut pas lire le registre — comme son jumeau
        `bf_budget_purchase.action_view_purchase_orders`."""
        line = self._budgeted_line()
        self.env.invalidate_all()
        with self.assertRaises(AccessError):
            line.with_user(self.no_register).action_view_subscriptions()

    def test_the_ids_of_the_register_are_not_readable_by_rpc(self):
        """🔴 Le trou que le garde de l'action seul laisserait ouvert : les
        deux champs qui portent des enregistrements sont relevés en SUDO, donc
        un `read` par RPC rendrait leurs ids. Le `groups` est AU CHAMP, pas
        seulement à la vue — un champ caché de l'écran répond quand même."""
        line = self._budgeted_line()
        self.env.invalidate_all()
        as_role = line.with_user(self.no_register)
        for field in ("subscription_ids", "subscription_no_calendar_ids"):
            with self.assertRaises(AccessError, msg=field):
                as_role.read([field])

    def test_the_line_form_carries_no_subscription_record_for_that_role(self):
        """L'arch servie à ce rôle ne porte ni les tags ni rien qui ouvre le
        registre — et porte toujours les sommes et le signal."""
        arch = (
            self.env["bf.budget.line"]
            .with_user(self.no_register)
            .get_views([(False, "form")])["views"]["form"]["arch"]
        )
        self.assertNotIn("subscription_ids", arch)
        self.assertNotIn("subscription_no_calendar_ids", arch)
        self.assertNotIn("action_view_subscriptions", arch)
        self.assertIn("subscription_upcoming", arch)
        self.assertIn("position_subscription_count", arch)
