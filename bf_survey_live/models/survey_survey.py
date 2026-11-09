# -*- coding: utf-8 -*-
import random
from urllib.parse import quote_plus

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from odoo.addons.bf_live_base.models.color_utils import brand_ramp, palette_color, text_color

from .bf_live_team import COULEURS, MAX_EQUIPES

# Le même hasard que la roue : celui du système, pas la suite pseudo-aléatoire
# par défaut, qui se rejoue à l'identique si on connaît sa graine.
_rng = random.SystemRandom()

# Les trois blocs du podium ne sont plus posés à la main : `brand_ramp` les
# calcule depuis la couleur de la SOCIÉTÉ du sondage, et retombe sur le bleu
# mesuré de la palette quand elle n'en a pas. L'encre de chaque bloc n'est pas
# choisie non plus : elle est calculée, et un essai relit qu'elle atteint 4,5:1
# sur les trois marches, pour douze teintes et le noir pur.


class SurveySurvey(models.Model):
    _inherit = "survey.survey"

    bf_live_scoreboard = fields.Selection(
        [("individual", "Par personne"), ("team", "Par équipe")],
        string="Classement de la séance", default="individual", required=True,
        help="Par équipe, l'écran de classement montre les équipes au lieu des "
             "personnes. Le moment où il s'affiche ne change pas.")
    bf_live_podium = fields.Boolean(
        string="Podium à la fin", default=True,
        help="Le dernier écran met les trois premiers sur des blocs, au lieu "
             "des barres du classement.")
    bf_live_team_target = fields.Integer(
        string="Nombre d'équipes à tirer", default=2)
    bf_live_team_ids = fields.One2many(
        "bf.live.team", "survey_id", string="Équipes")
    bf_live_team_count = fields.Integer(
        string="Nombre d'équipes", compute="_compute_bf_live_team_count")
    bf_live_join_badge = fields.Boolean(
        string="Code de participation à l'écran", default=True,
        help="Le code de la séance et son code QR, en bas à gauche de chaque "
             "écran projeté. Sans eux, un retardataire n'a plus rien à scanner "
             "dès la première question : le code QR de l'écran d'accueil "
             "disparaît avec lui.")
    bf_live_frame_counter = fields.Boolean(
        string="Compteur d'écrans", default=True,
        help="Le numéro de l'écran en cours, en bas de l'écran projeté. Ne "
             "paraît que si la séance porte une trame, donc au moins une "
             "diapositive.")

    @api.depends("bf_live_team_ids")
    def _compute_bf_live_team_count(self):
        for sondage in self:
            sondage.bf_live_team_count = len(sondage.bf_live_team_ids)

    # ------------------------------------------------------------
    # LES PARTICIPANTS DE LA SÉANCE EN COURS
    # ------------------------------------------------------------

    def _bf_live_participations(self):
        """Les gens présents à la séance en cours, et eux seuls.

        ⚠️ `session_start_time` borne la liste : sans elle, le tableau de ce midi
        compterait la salle d'hier. Et `test_entry` sort, contrairement au
        classement du cœur, qui compte les essais de l'animateur parmi les
        joueurs.

        Lu en sudo : les pseudonymes s'affichent déjà au classement devant toute
        la salle, donc rien ne s'ouvre de plus, et qui tient la séance n'a pas
        besoin des droits de Sondages sur le sondage d'un autre.
        """
        self.ensure_one()
        Participation = self.env["survey.user_input"].sudo()
        if not self.session_start_time:
            return Participation.browse()
        return Participation.search([
            ("survey_id", "=", self.id),
            ("is_session_answer", "=", True),
            ("create_date", ">=", self.session_start_time),
            ("test_entry", "=", False),
        ], order="create_date, id")

    # ------------------------------------------------------------
    # LES ÉQUIPES
    # ------------------------------------------------------------

    def _bf_team_question(self):
        """La question qui choisit l'équipe, s'il y en a une."""
        self.ensure_one()
        return self.question_ids.filtered(lambda question: question.bf_save_as_team)[:1]

    def action_start_session(self):
        """Une séance qui s'ouvre fige ses équipes.

        ⚠️ C'est ici et nulle part ailleurs : une réponse arrivant pendant la
        séance doit trouver son équipe déjà là, sans la créer elle-même. Deux
        participants qui répondent en même temps créeraient deux fois la même, et
        la contrainte d'unicité ferait tomber la réponse de l'un des deux.
        """
        resultat = super().action_start_session()
        self._bf_sync_teams()
        return resultat

    def _bf_sync_teams(self):
        """Une équipe par réponse de la question qui choisit l'équipe."""
        for sondage in self:
            question = sondage._bf_team_question()
            if not question:
                continue
            Equipe = sondage.env["bf.live.team"].sudo()
            existantes = {
                equipe.answer_id.id: equipe
                for equipe in sondage.bf_live_team_ids.sudo()
                if equipe.answer_id
            }
            for rang, reponse in enumerate(question.suggested_answer_ids):
                equipe = existantes.get(reponse.id)
                valeurs = {
                    "name": reponse.value or _("Équipe %(rang)s", rang=rang + 1),
                    "color_key": sondage._bf_color_key(rang),
                    "sequence": 10 * (rang + 1),
                }
                if equipe:
                    equipe.write(valeurs)
                else:
                    Equipe.create(dict(
                        valeurs, survey_id=sondage.id, answer_id=reponse.id))

    @api.model
    def _bf_color_key(self, rang):
        return COULEURS[rang % MAX_EQUIPES][0]

    def action_bf_draw_teams(self):
        """Répartit les gens présents en équipes, au hasard, à un membre près."""
        self.ensure_one()
        if not self.env.user.has_group("survey.group_survey_user"):
            raise AccessError(_("Seules les personnes du groupe Sondages tiennent une séance."))
        if self._bf_team_question():
            raise UserError(_(
                "Les équipes de « %(sondage)s » viennent de la question "
                "« %(question)s ». Pour les tirer au hasard, retirez d'abord le "
                "drapeau « choisit l'équipe » de cette question.",
                sondage=self.title, question=self._bf_team_question().title))
        nombre = self.bf_live_team_target
        if not 2 <= nombre <= MAX_EQUIPES:
            raise UserError(_(
                "Le tirage fait de 2 à %(max)s équipes : un tableau projeté au "
                "fond d'une salle ne se lit plus au-delà.", max=MAX_EQUIPES))
        participations = self._bf_live_participations()
        if not participations:
            raise UserError(_(
                "Personne n'a encore rejoint la séance de « %(sondage)s » : il "
                "n'y a personne à répartir.", sondage=self.title))
        equipes = self._bf_teams_for_draw(nombre)
        melange = list(participations)
        _rng.shuffle(melange)
        # Tourniquet : les équipes sortent égales à un membre près, quoi qu'il
        # arrive. Répartir au hasard sans tourniquet donne 7 contre 3 une fois
        # sur dix, et une équipe de 3 n'a plus de chance.
        for rang, participation in enumerate(melange):
            participation.sudo().bf_team_id = equipes[rang % nombre].id
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "message": _(
                    "%(gens)s participants répartis en %(equipes)s équipes.",
                    gens=len(melange), equipes=nombre),
                "next": {"type": "ir.actions.act_window_close"},
            },
        }

    def _bf_teams_for_draw(self, nombre):
        """Les équipes du tirage, créées au besoin, dans l'ordre de la palette."""
        self.ensure_one()
        Equipe = self.env["bf.live.team"].sudo()
        libres = self.bf_live_team_ids.sudo().filtered(
            lambda equipe: not equipe.answer_id).sorted(lambda e: (e.sequence, e.id))
        equipes = list(libres)[:nombre]
        for rang in range(len(equipes), nombre):
            equipes.append(Equipe.create({
                "name": Equipe._bf_team_name(rang),
                "survey_id": self.id,
                "color_key": self._bf_color_key(rang),
                "sequence": 10 * (rang + 1),
            }))
        return equipes

    # ------------------------------------------------------------
    # LE TABLEAU PROJETÉ
    # ------------------------------------------------------------

    def _bf_live_scoreboard(self, final=False):
        """Ce que le tableau projeté affiche, au moment où on le demande.

        Un rang par personne, ou un rang par équipe qui a quelqu'un dedans. Une
        équipe que personne n'a choisie ne prend pas de bloc à l'écran ; les gens
        sans équipe se comptent à part, pour que l'animateur les voie.
        """
        self.ensure_one()
        participations = self._bf_live_participations()
        sans_equipe = 0
        if self.bf_live_scoreboard == "team":
            mode = "team"
            avec = participations.filtered(lambda p: p.bf_team_id)
            sans_equipe = len(participations) - len(avec)
            lignes = [
                equipe._bf_board_row(participations)
                for equipe in avec.bf_team_id.sudo().sorted(lambda e: (e.sequence, e.id))
            ]
            lignes.sort(key=lambda ligne: (-ligne["average"], -ligne["score"], ligne["label"]))
        else:
            mode = "individual"
            lignes = []
            for participation in participations:
                points = participation.scoring_total
                lignes.append({
                    "label": (participation.nickname or "").strip() or _("Anonyme"),
                    "members": 1,
                    "score": points,
                    "average": points,
                })
            lignes.sort(key=lambda ligne: (-ligne["score"], ligne["label"]))
            for rang, ligne in enumerate(lignes):
                fond = palette_color(rang)
                ligne["color"] = fond
                ligne["text_color"] = text_color(fond)
        for rang, ligne in enumerate(lignes):
            ligne["rank"] = rang + 1
        podium = []
        rampe = self._bf_live_brand()["ramp"]
        for rang, ligne in enumerate(lignes[:3]):
            podium.append(dict(
                ligne, block_color=rampe[rang], block_text_color=text_color(rampe[rang])))
        return {
            "mode": mode,
            "final": bool(final),
            "rows": lignes,
            "podium": podium,
            "others": lignes[3:10],
            "teamless": sans_equipe,
            "attendees": len(participations),
        }

    def _bf_team_strip(self):
        """Les équipes de la séance, pour l'écran d'attente.

        L'écran d'attente est celui que la salle regarde en arrivant : le code,
        le code QR, le compte de ceux qui ont rejoint. Les équipes y manquaient,
        et c'est pourtant le seul moment de la séance où chacun a le temps de
        lire la sienne.

        ⚠️ Le compte des sans-équipe ne paraît QUE si quelqu'un a déjà une
        équipe. Avec des équipes nées d'une question, personne n'a encore
        répondu pendant l'attente : « 12 personnes sans équipe » y serait vrai,
        inévitable et sans intérêt. Après un tirage, le même chiffre dit enfin
        quelque chose : qui a rejoint la séance après le tirage.
        """
        self.ensure_one()
        participations = self._bf_live_participations()
        equipes = self.bf_live_team_ids.sudo().sorted(
            lambda equipe: (equipe.sequence, equipe.id))
        lignes = [equipe._bf_board_row(participations) for equipe in equipes]
        quelquun_a_une_equipe = any(ligne["members"] for ligne in lignes)
        return {
            "teams": lignes,
            "teamless": (len(participations.filtered(lambda p: not p.bf_team_id))
                         if quelquun_a_une_equipe else 0),
        }

    # ------------------------------------------------------------
    # LA MARQUE DE LA MAISON, ET LE CODE POUR REJOINDRE
    # ------------------------------------------------------------

    def _bf_live_brand(self):
        """La marque de la maison, telle que les écrans de la séance la portent.

        La société du sondage, et non une couleur écrite dans la feuille de
        style : un module vendable se brande chez son client. `primary_color`
        est le champ de la mise en page des documents, donc la couleur que la
        maison a déjà choisie une fois pour ses rapports et ses courriels.

        ⚠️ Le logo passe par `/logo.png?company=<id>`, la route du cœur, et
        l'image n'est posée que si la société EN A un : sans logo, cette route
        sert un carré gris de remplacement (`nologo.png`), qu'on afficherait au
        mur sans le savoir.

        La rampe retombe sur le bleu mesuré de la palette quand la société n'a
        pas de couleur, et pousse une teinte illisible jusqu'à ce qu'une encre
        l'atteigne : les deux décisions vivent dans `bf_live_base`, pas ici.
        """
        self.ensure_one()
        societe = self.env.company
        rampe = brand_ramp(societe.primary_color)
        return {
            "name": societe.name,
            "logo": "/logo.png?company=%s" % societe.id if societe.logo_web else False,
            "color": rampe[0],
            "text_color": text_color(rampe[0]),
            "ramp": rampe,
        }

    def _bf_join_badge(self):
        """Le code de la séance et son code QR, pour chaque écran projeté.

        Le cœur projette le code QR sur l'écran d'accueil, et plus jamais
        ensuite : dès la première question, qui arrive en retard n'a plus rien à
        scanner. Il lui reste l'adresse écrite en petit dans l'en-tête ; la carte
        y ajoute le code et son code QR, assez gros pour une salle.

        ⚠️ Le code QR est calculé ICI et non dans le gabarit : `quote_plus`
        n'est dans le contexte de QWeb que sur une page du site, et l'écran de
        séance se rend AUSSI par `ir.qweb._render`, hors requête de page, à
        chaque flèche de l'animateur. Un gabarit qui l'appellerait tomberait à
        la première flèche.

        ⚠️ Rien hors d'une séance en cours : l'écran d'accueil porte déjà son
        grand code QR, et une séance close n'a plus de code qui mène quelque
        part (`/s/<code>` répond alors « séance pas encore lancée »).
        """
        self.ensure_one()
        if not self.bf_live_join_badge or self.session_state != "in_progress":
            return False
        if not self.session_code or not self.session_link:
            return False
        return {
            "code": self.session_code,
            "link": self.session_link,
            "qr": "/report/barcode/QR/%s?width=360&height=360" % quote_plus(self.session_link),
        }

    # ------------------------------------------------------------
    # LA TRAME DE PRÉSENTATION
    # ------------------------------------------------------------

    def _bf_slides(self):
        """Les sections de ce sondage qui sont des diapositives."""
        self.ensure_one()
        return self.question_and_page_ids.filtered(
            lambda element: element._bf_is_slide())

    def _bf_has_frame(self):
        """Vrai si une séance ouverte projette une trame.

        La garde est `session_state`, et non `survey_type` : une séance tourne
        aussi sur un sondage de type « personnalisé », c'est la règle du cœur
        lui-même (`session_available`). Hors séance, le sondage se remplit comme
        avant : une diapositive y reste une section que le cœur saute, et le
        formulaire d'un répondant ne change pas d'un écran.
        """
        self.ensure_one()
        return bool(self.session_state) and bool(self._bf_slides())

    def _get_pages_and_questions_to_show(self):
        """Une diapositive reste dans la liste des écrans, même sans description.

        🔴 Le cœur écarte d'entrée toute section sans description. Une
        diapositive qui ne porte qu'un titre et une image tomberait donc de la
        liste, et `ids.index()` lèverait une ValueError sur le TÉLÉPHONE de
        chaque participant, pas seulement à l'écran de l'animateur :
        `_prepare_survey_data` demande l'écran précédant la question en cours,
        et la question en cours, c'est la diapositive au mur.
        """
        ecrans = super()._get_pages_and_questions_to_show()
        if not self.session_state:
            return ecrans
        ajouts = self._bf_slides()
        # 🔴 Et l'écran qui est AU MUR, quoi qu'il soit devenu. Changer la mise
        # en page d'une diapositive pour « standard » pendant la séance la sort
        # de la liste, et `_is_last_page_or_question` lève alors une ValueError
        # à la figure de l'animateur. Mesuré au banc : la page de séance rend
        # 500. Le cœur n'y arrive jamais seul (sa marche ne rend jamais une
        # section sans description), donc la garde est à nous.
        courant = self.session_question_id
        if courant and courant not in ecrans:
            ajouts |= courant
        if not ajouts:
            return ecrans
        # `sorted()` sans argument remet l'ordre du modèle (`sequence, id`),
        # donc l'ordre du document : une diapositive reprend sa place.
        return (ecrans | ajouts).sorted()

    def _get_next_page_or_question(self, user_input, page_or_question_id, go_back=False):
        """L'écran suivant, diapositives comprises."""
        suivant = super()._get_next_page_or_question(
            user_input, page_or_question_id, go_back=go_back)
        sondage = user_input.survey_id
        if not sondage or not sondage._bf_has_frame():
            return suivant
        return sondage._bf_slide_between(
            user_input, page_or_question_id, suivant, go_back) or suivant

    def _bf_slide_between(self, user_input, courant_id, suivant, go_back):
        """La diapositive que le cœur vient de sauter, s'il y en a une.

        On ne récrit pas sa marche : elle cherche une QUESTION à montrer et saute
        une section qui ne porte ni question ni description. On regarde donc
        l'intervalle entre l'écran courant et celui qu'elle propose : si une
        diapositive y est restée, c'est elle qui vient, et la plus proche
        d'abord, dans le sens du voyage.
        """
        self.ensure_one()
        Question = self.env["survey.question"]
        ecrans = self._get_pages_or_questions(user_input)
        if not courant_id or courant_id not in ecrans.ids:
            return Question
        courant = ecrans.ids.index(courant_id)
        if go_back:
            borne = (ecrans.ids.index(suivant.id)
                     if suivant and suivant.id in ecrans.ids else -1)
            return ecrans[borne + 1:courant].filtered(
                lambda ecran: ecran._bf_is_slide())[-1:]
        borne = (ecrans.ids.index(suivant.id)
                 if suivant and suivant.id in ecrans.ids else len(ecrans))
        return ecrans[courant + 1:borne].filtered(
            lambda ecran: ecran._bf_is_slide())[:1]

    def _is_first_page_or_question(self, page_or_question):
        """Le premier écran de la trame, et pas la première section décrite.

        🔴 Le cœur en décide par la description de la première section. Une trame
        qui ouvre sur une diapositive sans description lui ferait répondre non :
        la flèche de retour resterait offerte, et un retour depuis le PREMIER
        écran ferme la séance, parce que la route rend un dictionnaire vide et
        que l'écran prend ça pour la fin du sondage.
        """
        if not self._bf_has_frame():
            return super()._is_first_page_or_question(page_or_question)
        ecrans = self._get_pages_and_questions_to_show()
        return bool(ecrans) and page_or_question == ecrans[0]

    def _bf_frame_values(self, user_input=None):
        """La trame projetée : combien d'écrans, et lequel est au mur.

        Comptée avec la règle du cœur (`_get_pages_or_questions`) : les questions
        conditionnelles que rien n'a encore déclenchées n'y sont pas, donc le
        total peut bouger d'un écran à l'autre. C'est le prix d'un compte juste ;
        un total figé mentirait dès la première question conditionnelle.
        """
        self.ensure_one()
        vide = {"total": 0, "position": 0, "slides": 0}
        # ⚠️ `session_state` fait partie de la garde : une séance CLOSE garde sa
        # question en cours, mais la liste des écrans n'y porte plus les
        # diapositives (`_bf_has_frame` est faux), et le compteur aurait affiché
        # « 4 / 4 » sur une trame de sept écrans.
        if not self.session_state or not self.session_start_time:
            return vide
        if not self.question_and_page_ids:
            return vide
        if user_input is None:
            user_input = self._get_session_most_voted_answers()
        ecrans = self._get_pages_or_questions(user_input)
        if not ecrans:
            return vide
        courant = self.session_question_id
        return {
            "total": len(ecrans),
            "position": (ecrans.ids.index(courant.id) + 1
                         if courant and courant.id in ecrans.ids else 0),
            "slides": len(ecrans.filtered(lambda ecran: ecran._bf_is_slide())),
        }
