"""Le compte d'exploitation naît audible, et il peut refuser de l'être.

🔴 **Ce qui s'éprouve ici n'est pas que le réglage se pose, c'est qu'il ne SE
REPOSE PAS.** Poser `notification_type = 'inbox'` est facile ; il existe même un
chemin natif d'une ligne — faire impliquer `mail.group_mail_notification_type_inbox`
par le groupe Exploitation. Mesuré, ce chemin fait trois
choses, et la troisième disqualifie les deux premières :

1. il pose le mode sur les comptes neufs ;
2. il le pose rétroactivement sur ceux qui existent déjà ;
3. 🔴 **il le REPOSE.** La personne qui se remet en courriel garde la ligne
   d'appartenance au groupe, et comme `notification_type` se calcule sur
   `groups_id`, le premier recalcul venu la rebascule — un administrateur qui
   lui accorde n'importe quel autre groupe y suffit. Son choix s'affiche
   respecté jusqu'au jour où il ne l'est plus, sans un mot.

Le mode de notification est un réglage PERSONNEL. Un défaut à la création se
défend ; une reprise silencieuse, non. `test_le_refus_de_la_personne_tient` est
le test qui tient cette distinction : il passe au vert sur un défaut et il
ROUGIT sur une implication. Sans lui, les deux implémentations se ressemblent.
"""
from datetime import timedelta

from odoo import fields
from odoo.tests.common import TransactionCase, tagged

OPS_GROUP = "bf_property_operations.group_bf_property_operations"
INBOX_GROUP = "mail.group_mail_notification_type_inbox"


@tagged("post_install", "-at_install")
class TestOperationsNotification(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ops_group = cls.env.ref(OPS_GROUP)
        cls.inbox_group = cls.env.ref(INBOX_GROUP)
        cls.internal_group = cls.env.ref("base.group_user")

    def _user(self, login, group_xmlids=(), **extra):
        group_ids = [self.env.ref(xmlid).id for xmlid in group_xmlids]
        vals = {"name": login, "login": login}
        if group_ids:
            vals["groups_id"] = [(6, 0, group_ids)]
        vals.update(extra)
        return self.env["res.users"].create(vals)

    # ── Le défaut ──

    def test_un_compte_d_exploitation_neuf_nait_audible(self):
        """Sans lui, la liste du quart existe et le téléphone reste muet."""
        concierge = self._user(
            "concierge.p99@example.org", ("base.group_user", OPS_GROUP)
        )
        self.assertEqual(concierge.notification_type, "inbox")

    def test_le_groupe_venu_par_implication_compte_aussi(self):
        """Le gestionnaire de la suite tient aussi l'exploitation.

        Le groupe Exploitation n'est alors PAS en clair dans les vals : il
        arrive par `implied_ids`. Un contrôle qui ne lirait que les
        identifiants demandés le manquerait.
        """
        manager = self._user(
            "gestionnaire.p99@example.org",
            ("base.group_user", "bf_property_core.group_bf_property_manager"),
        )
        self.assertIn(self.ops_group, manager.groups_id)
        self.assertEqual(manager.notification_type, "inbox")

    def test_un_choix_exprime_a_la_creation_n_est_pas_ecrase(self):
        """Un défaut ne défait pas ce qui a été demandé."""
        concierge = self._user(
            "choix.p99@example.org",
            ("base.group_user", OPS_GROUP),
            notification_type="email",
        )
        self.assertEqual(concierge.notification_type, "email")

    def test_un_compte_hors_exploitation_n_est_pas_touche(self):
        """⚠️ Le contrôle qui prouve que le défaut DISCRIMINE.

        Sans lui, un défaut posé sur tout le monde passerait les autres tests.
        """
        employe = self._user("employe.p99@example.org", ("base.group_user",))
        self.assertEqual(employe.notification_type, "email")

    def test_un_compte_de_portail_n_est_jamais_bascule(self):
        """La boîte Odoo est refusée aux comptes externes par Odoo lui-même
        (contrainte SQL) : le défaut ne doit pas aller s'y cogner."""
        occupant = self._user("occupant.p99@example.org", ("base.group_portal",))
        self.assertTrue(occupant.share)
        self.assertEqual(occupant.notification_type, "email")

    # ── 🔴 Le refus, et le fait qu'il TIENNE ──

    def test_le_refus_de_la_personne_tient(self):
        """🔴 LE test de ce module sur la notification.

        Il rougit sur une implication de groupe et passe sur un défaut. Trois
        affirmations, et la troisième est celle qui coûte :

        1. la personne peut se remettre en courriel depuis SON compte ;
        2. la ligne d'appartenance au groupe de la boîte Odoo s'en va vraiment
           — sous une implication, elle survit au geste et laisse la colonne et
           le groupe se contredire ;
        3. un geste d'administration SANS RAPPORT ne la rebascule pas. C'est
           là, et seulement là, que l'implication se trahit : elle recalcule
           `notification_type` sur `groups_id` et reprend le choix en silence.
        """
        concierge = self._user(
            "refus.p99@example.org", ("base.group_user", OPS_GROUP)
        )
        self.assertEqual(concierge.notification_type, "inbox")

        concierge.with_user(concierge).write({"notification_type": "email"})
        concierge.invalidate_recordset()
        self.assertEqual(concierge.notification_type, "email")
        self.assertNotIn(
            self.inbox_group,
            concierge.groups_id,
            "La ligne d'appartenance doit partir avec le choix. Si elle reste, "
            "la colonne et le groupe se contredisent et le prochain recalcul "
            "tranchera contre la personne.",
        )

        concierge.write(
            {"groups_id": [(4, self.env.ref("base.group_partner_manager").id)]}
        )
        concierge.invalidate_recordset()
        self.assertEqual(
            concierge.notification_type,
            "email",
            "Un groupe accordé pour tout autre chose vient de reprendre à "
            "quelqu'un un réglage personnel, sans le lui dire.",
        )

    def test_un_compte_deja_ouvert_n_est_pas_bascule_apres_coup(self):
        """⚠️ La contrepartie assumée du défaut à la création.

        Un compte qui existe déjà porte peut-être un choix délibéré. Lui donner
        le groupe Exploitation ne le change pas — c'est la lecture du quart qui
        le nomme, pour qu'on aille le demander à la personne.
        """
        employe = self._user("ancien.p99@example.org", ("base.group_user",))
        self.assertEqual(employe.notification_type, "email")
        employe.write({"groups_id": [(4, self.ops_group.id)]})
        employe.invalidate_recordset()
        self.assertEqual(employe.notification_type, "email")

    # ── La lecture du quart ──

    def _shift(self, users):
        organisation = self.env["bf.property.organisation"].create(
            {"name": "Syndicat du quart", "fraction_base": 1000}
        )
        building = self.env["bf.property.building"].create(
            {"name": "Immeuble du quart", "organisation_id": organisation.id}
        )
        team = self.env["maintenance.team"].create({"name": "Équipe du quart"})
        building.bf_maintenance_team_id = team
        now = fields.Datetime.now()
        return self.env["bf.property.shift"].create(
            {
                "date_start": now,
                "date_stop": now + timedelta(hours=8),
                "maintenance_team_id": team.id,
                "building_id": building.id,
                "user_ids": [(6, 0, users.ids)],
            }
        )

    def test_le_quart_nomme_les_comptes_muets(self):
        """La liste existe, le téléphone est muet, et maintenant ça se voit."""
        audible = self._user("audible.p99@example.org", ("base.group_user", OPS_GROUP))
        muet = self._user("muet.p99@example.org", ("base.group_user",))
        muet.write({"groups_id": [(4, self.ops_group.id)]})
        shift = self._shift(audible | muet)
        self.assertEqual(shift.silent_user_ids, muet)
        self.assertIn(muet.name, shift.silent_notice)
        self.assertNotIn(audible.name, shift.silent_notice)

    def test_le_quart_ne_dit_rien_quand_tout_le_monde_est_joignable(self):
        """⚠️ Le contrôle qui prouve que l'avertissement DISCRIMINE.

        Un bandeau permanent ne dit plus rien : c'est son absence qui donne du
        sens à sa présence.
        """
        audible = self._user("seul.p99@example.org", ("base.group_user", OPS_GROUP))
        shift = self._shift(audible)
        self.assertFalse(shift.silent_user_ids)
        self.assertFalse(shift.silent_notice)

    def test_un_concierge_lit_l_avertissement_de_son_propre_quart(self):
        """Le bandeau se lit par celui à qui il est destiné.

        ⚠️ Ce test ne garde AUCUN `sudo` : il n'y en a pas, et le harnais l'a
        prouvé. La lecture de `notification_type` sur un collègue passe par
        l'ACL ordinaire de `res.users`, qui accorde la lecture à tout interne —
        `SELF_READABLE_FIELDS` élargit ce qu'on lit sur soi, il ne restreint pas
        ce qu'on lit sur autrui. Ce qui s'éprouve ici est donc l'énoncé utile :
        le concierge affecté au quart voit l'avertissement de son propre quart,
        et il rougirait si la lecture se refermait un jour."""
        concierge = self._user(
            "lecteur.p99@example.org", ("base.group_user", OPS_GROUP)
        )
        collegue = self._user("collegue.p99@example.org", ("base.group_user",))
        collegue.write({"groups_id": [(4, self.ops_group.id)]})
        shift = self._shift(concierge | collegue)
        vu = shift.with_user(concierge)
        vu.invalidate_recordset()
        self.assertIn(collegue.name, vu.silent_notice)

    # ── 🔴 Le chemin de l'écran ──
    #
    # Tous les tests ci-dessus passent par `groups_id`. L'écran des
    # utilisateurs, lui, poste les champs REIFIÉS que `_update_user_groups_view`
    # fabrique, et c'est `UsersView.create` de `base` qui les convertit — après
    # cette surcharge dans le MRO. Le défaut ne se voyait donc que d'ici : par
    # `groups_id` le compte naissait en boîte Odoo, par l'écran il naissait
    # muet, avec le groupe pourtant accordé.

    def _reified_field_name(self):
        """Le nom du champ reifié du groupe Exploitation, LU dans l'arch.

        ⚠️ Il porte l'identifiant du groupe, qui change d'une base à l'autre :
        le taper à la main ferait un test qui passe ici et nulle part ailleurs.
        """
        import re

        arch = self.env["res.users"].get_view(
            self.env.ref("base.view_users_form").id, "form"
        )["arch"]
        names = set(re.findall(r'name="((?:sel_groups|in_group)_[0-9_]+)"', arch))
        for name in sorted(names):
            if str(self.ops_group.id) in name.split("_"):
                return name
        return None

    def test_le_compte_cree_par_l_ecran_nait_audible_aussi(self):
        """🔴 LE test qui manquait : le seul chemin par lequel un concierge est
        réellement créé."""
        field = self._reified_field_name()
        self.assertTrue(
            field, "Le groupe Exploitation n'a aucun champ reifié dans l'arch."
        )
        vals = {"name": "Concierge écran", "login": "concierge.ecran@example.org"}
        vals[field] = True if field.startswith("in_group_") else self.ops_group.id
        concierge = self.env["res.users"].create(vals)
        self.assertIn(self.ops_group, concierge.groups_id)
        self.assertEqual(concierge.notification_type, "inbox")

    def test_le_choix_exprime_tient_aussi_par_l_ecran(self):
        """⚠️ Le contrôle qui prouve que le nouveau chemin n'écrase rien."""
        field = self._reified_field_name()
        vals = {
            "name": "Concierge écran, choix fait",
            "login": "concierge.ecran.choix@example.org",
            "notification_type": "email",
        }
        vals[field] = True if field.startswith("in_group_") else self.ops_group.id
        concierge = self.env["res.users"].create(vals)
        self.assertEqual(concierge.notification_type, "email")

    def test_un_compte_hors_exploitation_cree_par_l_ecran_n_est_pas_touche(self):
        """⚠️ Le contrôle qui prouve que le nouveau chemin DISCRIMINE.

        Sans lui, un lecteur de champs reifiés trop large basculerait n'importe
        quel compte créé par l'écran, et les autres tests ne le diraient pas.
        """
        import re

        arch = self.env["res.users"].get_view(
            self.env.ref("base.view_users_form").id, "form"
        )["arch"]
        names = sorted(set(re.findall(r'name="(in_group_[0-9]+)"', arch)))
        stranger = [
            name for name in names
            if name != f"in_group_{self.ops_group.id}"
            and name != f"in_group_{self.inbox_group.id}"
        ]
        self.assertTrue(stranger, "Aucun autre groupe reifié à opposer.")
        employe = self.env["res.users"].create(
            {
                "name": "Employé écran",
                "login": "employe.ecran@example.org",
                stranger[0]: True,
            }
        )
        self.assertEqual(employe.notification_type, "email")
