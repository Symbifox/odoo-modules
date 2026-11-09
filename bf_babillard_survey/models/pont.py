# -*- coding: utf-8 -*-
from markupsafe import Markup

from odoo import _, fields, models
from odoo.exceptions import AccessError, UserError


class Survey(models.Model):
    _inherit = "survey.survey"

    def action_annoncer_au_babillard(self):
        """Poser au fil la carte d'un sondage Odoo, une seule fois.

        🔴 Publique, donc appelable par RPC : le `groups=` du bouton ne garde
        que l'écran. Sans ce contrôle, n'importe quelle personne interne
        publiait au babillard de toute la maison en passant par ici.
        """
        self.ensure_one()
        if not self.env.user.has_group("bf_babillard.group_babillard_redacteur"):
            raise AccessError(_("Seule la rédaction publie au babillard."))
        if not self.active:
            raise UserError(_("Un sondage archivé ne s'annonce pas."))
        # 🔴 Le lien de départ d'un sondage « sur invitation » ne mène nulle
        # part sans le jeton personnel du répondant : la carte, elle, est lue
        # par toute l'audience. Mieux vaut refuser que publier un lien mort.
        if self.access_mode == "token":
            raise UserError(_(
                "Ce sondage est réservé aux personnes invitées : son lien ne "
                "fonctionne qu'avec l'invitation. Passez-le en « Toute personne "
                "disposant du lien » pour l'annoncer au fil."))

        base = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
        lien = "%s%s" % (base, self.get_start_url())
        langue = self.env["bf.babillard.post"]._langue_de_la_maison()
        moi = self.with_context(lang=langue)

        corps = Markup("<p>%s</p><p><a href=\"%s\" class=\"btn btn-primary\">%s</a></p>") % (
            moi.env._("Un sondage vous attend : %(titre)s.", titre=self.title),
            lien,
            moi.env._("Répondre au sondage"),
        )
        carte = self.env["bf.babillard.post"]._depuis_source(
            "survey.survey", self.id, {
                "name": self.title,
                "type_publication": "annonce",
                "audience": "tous",
                "corps_html": corps,
                "company_id": self.env.company.id,
            })
        return {
            "type": "ir.actions.act_window",
            "res_model": "bf.babillard.post",
            "res_id": carte.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_annoncer_seance_au_babillard(self):
        """Annoncer au fil la séance en direct OUVERTE d'un sondage, avec son code.

        Le bouton d'à côté annonce le sondage ; celui-ci annonce la SÉANCE, qui
        n'est pas la même chose : elle a lieu maintenant, on la rejoint par un
        court code numérique, et le lien de départ du sondage n'y mène même pas.

        Sans ça, rien n'annonce une séance ouverte à la maison : l'animateur
        écrit le code dans un message, ou personne n'arrive.

        ⚠️ Quatre chiffres, et non cinq : mesuré dans `_generate_session_codes`,
        le cœur tire à quatre et n'en ajoute un que sur collision. Rien n'en
        dépend ici, mais c'est ce que la carte écrit à la salle.

        🔴 Publique, donc appelable par RPC : le `groups=` du bouton ne garde
        que l'écran.
        """
        self.ensure_one()
        if not self.env.user.has_group("bf_babillard.group_babillard_redacteur"):
            raise AccessError(_("Seule la rédaction publie au babillard."))
        if not self.active:
            raise UserError(_("Un sondage archivé ne s'annonce pas."))
        if self.session_state not in ("ready", "in_progress"):
            raise UserError(_(
                "Aucune séance n'est ouverte sur « %(titre)s » : il n'y a pas de "
                "code à annoncer. Ouvrez la séance, puis revenez.", titre=self.title))
        # 🔴 Les deux refus du cœur lui-même, mesurés dans
        # `_fetch_from_session_code` et `_check_validity` : un code de séance ne
        # mène NULLE PART sur une certification, ni sur un sondage réservé aux
        # personnes invitées (le code renvoie au lien de départ, qui réclame le
        # jeton nominatif). Mieux vaut refuser que poser un code mort au fil.
        if self.certification:
            raise UserError(_(
                "« %(titre)s » est une certification : le cœur refuse les codes "
                "de séance sur une certification, et le code annoncé ne mènerait "
                "personne nulle part.", titre=self.title))
        if self.access_mode == "token":
            raise UserError(_(
                "Ce sondage est réservé aux personnes invitées : le code de la "
                "séance renvoie au lien de départ, qui réclame l'invitation "
                "nominative. Passez-le en « Toute personne disposant du lien » "
                "pour annoncer la séance."))

        base = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
        lien = self.session_link or "%s/s/%s" % (base, self.session_code)
        langue = self.env["bf.babillard.post"]._langue_de_la_maison()
        moi = self.with_context(lang=langue)

        corps = Markup(
            "<p>%s</p><p>%s <strong>%s</strong></p>"
            "<p><a href=\"%s\" class=\"btn btn-primary\">%s</a></p>") % (
            moi.env._("La séance « %(titre)s » est ouverte.", titre=self.title),
            moi.env._("Code pour rejoindre :"),
            self.session_code,
            lien,
            moi.env._("Rejoindre la séance"),
        )
        valeurs = {
            "name": self.title,
            "type_publication": "annonce",
            "audience": "tous",
            "corps_html": corps,
            "company_id": self.env.company.id,
        }
        Carte = self.env["bf.babillard.post"].sudo()
        source = [("source_model", "=", "survey.survey"), ("source_res_id", "=", self.id)]
        Cartes = Carte.with_context(active_test=False)
        # ⚠️ Le babillard ne garde qu'UNE carte par source (contrainte
        # d'unicité sur le couple modèle/identifiant). La deuxième séance d'un
        # même sondage, un mois plus tard, ne peut donc pas poser une deuxième
        # carte : elle réécrit celle du sondage avec le code du jour et la remet
        # en tête du fil. Une carte qui garderait le code du mois dernier serait
        # pire qu'absente.
        #
        # 🔴 La carte cherchée est celle de la société COURANTE, et c'est le
        # `sudo()` ci-dessus qui l'exige : un sondage du cœur ne porte aucun
        # `company_id`, donc tous les sondages se voient de toutes les sociétés,
        # et `bf_babillard.rule_post_societe` — le seul rempart — ne s'applique
        # pas sous `sudo`. Sans ce filtre, la carte posée par la société B était
        # trouvée, écrasée, puis DÉPLACÉE en société A par le `company_id` des
        # valeurs. Mesuré sur la production : 4 sociétés actives, 3 cartes au
        # babillard, aucune issue d'un sondage — le cas ne s'est pas encore
        # produit.
        carte = Cartes.search(
            source + [("company_id", "=", self.env.company.id)], limit=1)
        if not carte:
            # Refuser plutôt qu'écrire : la contrainte `source_unique` ne connaît
            # pas la société, donc créer ici ferait remonter une IntegrityError
            # brute à la figure de la rédaction, sans dire ce qui bloque.
            ailleurs = Cartes.search(source, limit=1)
            if ailleurs:
                raise UserError(_(
                    "La séance de « %(titre)s » est déjà annoncée au babillard de "
                    "« %(societe)s », et le fil ne garde qu'une carte par sondage. "
                    "Passez dans cette société pour y annoncer la séance du jour.",
                    titre=self.title, societe=ailleurs.company_id.display_name))
        if carte:
            # 🔴 L'échéance repart à vide, et ce n'est pas du ménage : le cron
            # quotidien `_cron_echoir` repasse à « échue » toute carte publiée
            # dont l'échéance est passée. Une carte du mois dernier, échue le 3,
            # republiée ce matin pour la séance de 10 h, retombait du fil au
            # prochain passage du cron — pendant la séance.
            carte.write(dict(
                valeurs, active=True, state="publie", date_echeance=False,
                date_publication=fields.Datetime.now()))
        else:
            carte = Carte._depuis_source("survey.survey", self.id, valeurs)
        return {
            "type": "ir.actions.act_window",
            "res_model": "bf.babillard.post",
            "res_id": carte.id,
            "view_mode": "form",
            "target": "current",
        }
