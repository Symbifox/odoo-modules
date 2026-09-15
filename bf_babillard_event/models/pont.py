# -*- coding: utf-8 -*-
from odoo import api, fields, models


class Event(models.Model):
    _inherit = "event.event"

    @api.model_create_multi
    def create(self, vals_list):
        evenements = super().create(vals_list)
        evenements._babillard_suivre()
        return evenements

    def write(self, vals):
        resultat = super().write(vals)
        if {"name", "date_begin", "stage_id", "active", "company_id"} & set(vals):
            self._babillard_suivre()
        return resultat

    def unlink(self):
        cartes = self._babillard_cartes()
        resultat = super().unlink()
        cartes.filtered(lambda c: c.state == "publie").action_retirer()
        return resultat

    def _babillard_cartes(self):
        # ⚠️ `active_test=False` : une carte archivée existe toujours pour la
        # contrainte d'unicité. Sans cela, la sauvegarde de l'événement tombait.
        return self.env["bf.babillard.post"].sudo().with_context(
            active_test=False).search([
                ("source_model", "=", "event.event"), ("source_res_id", "in", self.ids)])

    def _babillard_annoncable(self):
        """Un événement actif, à venir, dont l'étape n'est pas une fin.

        🔴 `event.event` n'a **plus de champ `state`** en Odoo 18 : le cycle de
        vie passe par `stage_id` et son drapeau `pipe_end` (terminé, annulé). Un
        pont accroché à `state` lève « Invalid field 'state' » à la première
        écriture.
        """
        self.ensure_one()
        evenement = self.sudo()
        return bool(evenement.active and evenement.date_begin
                    and evenement.date_begin >= fields.Datetime.now()
                    and not evenement.stage_id.pipe_end)

    def _babillard_valeurs(self, creation=False):
        self.ensure_one()
        evenement = self.sudo()
        langue = self.env["bf.babillard.post"]._langue_de_la_maison()
        jour = fields.Datetime.context_timestamp(evenement, evenement.date_begin)
        valeurs = {
            "name": evenement.with_context(lang=langue).env._(
                "%(nom)s, le %(quand)s", nom=evenement.name,
                quand=jour.strftime("%d/%m/%Y")),
            # La carte tombe le lendemain : une invitation passée encombre.
            "date_echeance": fields.Date.add(jour.date(), days=1),
        }
        if creation:
            # La société ne se fixe qu'à la création : sinon la carte changeait de
            # société au gré de celle qui est active chez qui modifie l'événement.
            valeurs["company_id"] = evenement.company_id.id or self.env.company.id
        return valeurs

    def _babillard_suivre(self):
        """Tenir la carte du fil à jour de son événement.

        🔴 La carte ne suivait que la création : un événement renommé, déplacé ou
        annulé laissait au fil un titre, une date ou une invitation qui n'étaient
        plus vrais. La carte naît quand l'événement est à venir ; un nouveau nom
        ou une nouvelle date la suit ; un événement annulé, terminé, archivé ou
        passé la retire du fil. Une carte retirée à la main n'y revient jamais.
        """
        Post = self.env["bf.babillard.post"]
        cartes = {carte.source_res_id: carte for carte in self._babillard_cartes()}
        for evenement in self:
            carte = cartes.get(evenement.id)
            if not evenement._babillard_annoncable():
                if carte and carte.state == "publie":
                    carte.action_retirer()
                continue
            if not carte:
                Post._depuis_source("event.event", evenement.id, dict(
                    evenement._babillard_valeurs(creation=True),
                    type_publication="evenement", audience="tous"))
                continue
            if carte.state != "publie" or not carte.active:
                # 🔴 Une carte retirée par la modération, ou remise en brouillon par
                # la rédaction, ne revient pas d'elle-même : le pont annonce, il ne
                # défait pas une décision humaine.
                continue
            carte.write(evenement._babillard_valeurs())
