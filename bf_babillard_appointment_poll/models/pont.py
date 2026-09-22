# -*- coding: utf-8 -*-
from markupsafe import Markup

from odoo import _, fields, models
from odoo.exceptions import AccessError, UserError


class AppointmentPoll(models.Model):
    _inherit = "appointment.poll"

    def action_annoncer_au_babillard(self):
        """Poser au fil la carte d'un sondage de disponibilités, une seule fois.

        🔴 Publique, donc appelable par RPC : le `groups=` du bouton ne garde
        que l'écran.
        """
        self.ensure_one()
        if not self.env.user.has_group("bf_babillard.group_babillard_redacteur"):
            raise AccessError(_("Seule la rédaction publie au babillard."))
        if self.state != "open":
            raise UserError(_(
                "Seul un sondage en cours s'annonce : celui-ci est « %(etat)s ».",
                etat=dict(self._fields["state"].selection).get(self.state, self.state)))

        langue = self.env["bf.babillard.post"]._langue_de_la_maison()
        moi = self.with_context(lang=langue)
        lignes = [Markup("<p>%s</p>") % moi.env._(
            "Un sondage de disponibilités cherche des réponses : %(nom)s.",
            nom=self.name)]
        if self.close_date:
            lignes.append(Markup("<p>%s</p>") % moi.env._(
                "Il ferme le %(quand)s.",
                quand=fields.Datetime.context_timestamp(
                    self, self.close_date).strftime("%d/%m/%Y")))
        # 🔴 Le lien d'inscription libre est le SEUL lien public de ce module.
        # Le lien de vote, lui, est propre à un participant : il n'a rien à
        # faire sur une carte que toute la maison lit.
        if self.self_signup and self.signup_url:
            lignes.append(
                Markup("<p><a href=\"%s\" class=\"btn btn-primary\">%s</a></p>") % (
                    self.signup_url, moi.env._("Donner mes disponibilités")))
        else:
            lignes.append(Markup("<p>%s</p>") % moi.env._(
                "Chaque personne répond par son propre lien : demandez le vôtre "
                "à %(qui)s.", qui=self.user_id.name or self.create_uid.name))

        carte = self.env["bf.babillard.post"]._depuis_source(
            "appointment.poll", self.id, {
                "name": self.name,
                "type_publication": "annonce",
                "audience": "tous",
                "corps_html": Markup("").join(lignes),
                "company_id": self.company_id.id or self.env.company.id,
                # La carte tombe avec le sondage : une invitation à répondre
                # après la fermeture n'annonce plus rien.
                "date_echeance": (
                    fields.Datetime.context_timestamp(self, self.close_date).date()
                    if self.close_date else False),
            })
        return {
            "type": "ir.actions.act_window",
            "res_model": "bf.babillard.post",
            "res_id": carte.id,
            "view_mode": "form",
            "target": "current",
        }
