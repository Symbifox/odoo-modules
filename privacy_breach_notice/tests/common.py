from odoo.tests import TransactionCase


class BreachNoticeCase(TransactionCase):
    """Un client avec son RPRP désigné, et les rôles réels : jamais l'administrateur."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Partner = cls.env["res.partner"]
        cls.org = Partner.create({"name": "Client Essai", "is_company": True,
                                  "email": "info@client-essai.invalid"})
        cls.rprp = Partner.create({"name": "Directrice Essai", "parent_id": cls.org.id,
                                   "email": "dg@client-essai.invalid"})
        cls.org.write({"privacy_officer_partner_id": cls.rprp.id,
                       "privacy_officer_email": "rprp@client-essai.invalid"})
        Users = cls.env["res.users"].with_context(no_reset_password=True)
        base = cls.env.ref("base.group_user")
        cls.manager = Users.create({
            "name": "Gestionnaire VP", "login": "gvp-essai@essai.invalid", "email": "gvp@essai.invalid",
            "groups_id": [(6, 0, [base.id, cls.env.ref("privacy_consent.group_privacy_manager").id])]})
        cls.manager.partner_id.function = "Responsable de la sécurité"
        cls.reader = Users.create({
            "name": "Lecteur VP", "login": "lvp-essai@essai.invalid",
            "groups_id": [(6, 0, [base.id, cls.env.ref("privacy_consent.group_privacy_user").id])]})
        cls.Notice = cls.env["privacy.breach.notice"].with_user(cls.manager)

    def setUp(self):
        super().setUp()
        # Le curseur de la classe porte l'ensemble des messages « frais » : chaque essai part à vide.
        self.env.cr._breach_fresh_messages = set()

    def assertUserErrorNotAccess(self, fn, *args, **kwargs):
        """🔴 AccessError hérite de UserError : un refus de droits rendrait l'essai vert pour la
        mauvaise raison. On exige une UserError qui n'est PAS un refus de droits."""
        from odoo.exceptions import AccessError, UserError
        with self.assertRaises(UserError) as cm:
            fn(*args, **kwargs)
        self.assertNotIsInstance(cm.exception, AccessError)
        return cm.exception

    def _breach(self, **vals):
        data = {
            "responsible_id": self.org.id,
            "notice_type": "breach",
            "nature": "unauthorized_access",
            "circumstances": "Un mot de passe d'application exposé dans un journal.",
            "discovered_at": "2026-10-07 14:00:00",
            "pi_description": "Coordonnées professionnelles des employés.",
            "subject_count": 12, "subject_count_quebec": 12,
            "measures_taken": "2026-10-07 : mot de passe changé, journal purgé.",
        }
        data.update(vals)
        return self.Notice.create(data)
