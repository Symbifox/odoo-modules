# -*- coding: utf-8 -*-
"""Un groupe de signataires : à qui l'on tend la carte.

Un lien qu'on affiche dans un canal touche qui passe par là ; un groupe
touche qui l'on a choisi. Le groupe se réutilise d'une carte à l'autre
(« Équipe de Montréal », « Direction », « Clients du dossier X »), et il se
résout au moment de l'envoi, pas à sa création : une personne arrivée dans le
service depuis reçoit l'invitation, une personne partie ne la reçoit pas.

Trois sources, parce que les trois existent au bureau : des personnes une par
une, des services entiers, et des contacts hors de l'entreprise. Le module ne
dépend pas des groupes de destinataires de la messagerie (`bf.recipient.group`)
pour ne pas tirer tout `bf_email_management` sur un locataire qui n'en veut
pas ; un pont pourra les relire plus tard.

⚠️ Ce que le groupe NE fait pas : dire qui a signé. La signature est libre
et sans compte, donc « qui n'a pas encore signé » n'est pas une donnée qu'on
possède, et c'est très bien ainsi. Relancer, c'est réécrire au groupe entier.
"""

from odoo import api, fields, models


class CelebrationSignerGroup(models.Model):
    _name = "bf.celebration.signer.group"
    _description = "Groupe de signataires"
    _order = "name"

    name = fields.Char(string="Nom", required=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company", string="Société",
        default=lambda self: self.env.company)

    employee_ids = fields.Many2many(
        "hr.employee", "bf_celebration_signer_group_employee_rel",
        "group_id", "employee_id", string="Personnes")
    department_ids = fields.Many2many(
        "hr.department", "bf_celebration_signer_group_department_rel",
        "group_id", "department_id", string="Services",
        help="Tous les membres actifs du service, lus au moment de l'envoi.")
    partner_ids = fields.Many2many(
        "res.partner", "bf_celebration_signer_group_partner_rel",
        "group_id", "partner_id", string="Contacts",
        help="Des gens hors de l'entreprise : un client, un partenaire.")

    member_count = fields.Integer(
        string="Adresses", compute="_compute_member_count")
    note = fields.Text(string="Note")

    _sql_constraints = [
        ("name_company_uniq", "unique(name, company_id)",
         "Un groupe de signataires porte déjà ce nom."),
    ]

    @api.depends("employee_ids", "department_ids", "partner_ids")
    def _compute_member_count(self):
        for groupe in self:
            groupe.member_count = len(groupe._resoudre())

    def _resoudre(self):
        """Les adresses que ce ou ces groupes désignent, dédoublonnées.

        Rend un dictionnaire ``adresse en minuscules -> (nom, adresse)``. Une
        personne sans adresse est ignorée : on ne peut pas lui tendre la
        carte, et l'annoncer n'aiderait personne. Les employés sont lus en
        sudo parce que le `work_email` d'un collègue d'un autre service peut
        être hors de portée de l'organisateur ; c'est la seule donnée lue.
        """
        trouves = {}

        def ajouter(nom, adresse):
            adresse = (adresse or "").strip()
            if adresse and "@" in adresse:
                trouves.setdefault(adresse.lower(), (nom or adresse, adresse))

        for groupe in self:
            employes = groupe.employee_ids.sudo()
            for service in groupe.department_ids.sudo():
                employes |= service.member_ids
            for emp in employes.filtered("active"):
                ajouter(emp.name, emp.work_email)
            for partenaire in groupe.partner_ids.filtered("active"):
                ajouter(partenaire.name, partenaire.email)
        return trouves
