"""Section « Consommation Claude » du digest quotidien.

Même patron d'injection que `bf_hosting_patch_digest` : bâtir le HTML, puis le
glisser devant le marqueur `<!-- Divider -->` du gabarit de base.

Contrairement aux autres satellites, cette section ne se tait pas les jours
calmes : c'est un compteur qu'on veut lire chaque matin, pas une alerte. Elle
se tait seulement quand il n'y a aucun compte, c'est-à-dire sur un locataire où
la sonde ne verse pas.

⚠️ Le silence a pourtant un piège ici aussi. Un relevé qui ne bouge plus garde
ses dernières fenêtres, et `_juger` les déclare encore « sous les seuils » :
un compteur figé se lirait comme un compteur rassurant. La fraîcheur du relevé
est donc jugée ICI, et un relevé périmé passe en rouge.

🔴 La section se lit AU NOM DU DESTINATAIRE, pas en `sudo`. Les comptes ne sont
lisibles qu'aux administrateurs ; le digest part d'une tâche planifiée, donc
d'un superutilisateur, et une lecture sans changer d'usager donnerait les
comptes et leur consommation à n'importe quel destinataire.
"""

import logging
from datetime import timedelta

from markupsafe import Markup, escape as _esc

from odoo import _, api, fields, models
from odoo.addons.bf_claude_chat.models.claude_account import FENETRES

_logger = logging.getLogger(__name__)

ADMIN = "base.group_system"

# La sonde verse aux deux heures. Trois passes manquées, et le relevé ne dit
# plus rien du présent.
RELEVE_PERIME_H = 6

ANTHRACITE = "#2E3132"
BLEU = "#29ABE2"
ROUGE = "#C0392B"
ORANGE = "#D68910"
GRIS = "#6b7280"

# Au plus tant de conversations montrées par paquet.
GEN_SUIVI_PAR_PAQUET = 8

ORDRE_FENETRES = {cle: rang for rang, (cle, _libelle) in enumerate(FENETRES)}
LIBELLES_FENETRES = dict(FENETRES)


def _pourcent(valeur):
    # Espace insécable avant le signe, comme le veut l'usage en français.
    return f"{valeur:.0f}\u00a0%"


def _delai(avant, maintenant):
    """« dans 1 j 3 h », « dans 5 h 12 min », « dans 42 min », ou '' si passé.

    Arrondi à la minute AVANT de découper : une bascule à deux jours pile moins
    quelques millisecondes se lirait sinon « dans 1 j 23 h ».
    """
    minutes = round((avant - maintenant).total_seconds() / 60)
    if minutes <= 0:
        return ""
    jours, reste = divmod(minutes, 24 * 60)
    heures, minutes = divmod(reste, 60)
    if jours:
        return f"dans {jours}\u00a0j {heures}\u00a0h" if heures else f"dans {jours}\u00a0j"
    if heures:
        return f"dans {heures}\u00a0h {minutes:02d}\u00a0min" if minutes else f"dans {heures}\u00a0h"
    return f"dans {minutes}\u00a0min"


class DailyDigestConfig(models.Model):
    _inherit = "daily.digest.config"

    include_claude_usage = fields.Boolean(
        string="Inclure la consommation Claude", default=True,
    )
    # Ce qui attend quelque chose dans Gen (à fermer, t'attend,
    # relancé). Muette tant que le locataire n'a pas allumé la fermeture.
    include_gen_followups = fields.Boolean(
        string="Inclure les conversations Gen à suivre", default=True,
    )

    # ------------------------------------------------------------- collecte
    def _claude_usage_comptes(self, user):
        """Les comptes actifs, tels que le DESTINATAIRE a le droit de les voir."""
        if not user or not user.has_group(ADMIN):
            return self.env["claude.account"].browse()
        return self.env["claude.account"].with_user(user).search([])

    def _claude_usage_perime(self, compte, maintenant):
        """Le diagnostic d'un relevé qui ne dit plus le présent, ou ''."""
        if compte.etat_sonde != "ok":
            libelle = dict(compte._fields["etat_sonde"]._description_selection(
                self.env)).get(compte.etat_sonde, compte.etat_sonde)
            if compte.message_sonde:
                return f"{libelle} : {compte.message_sonde}"
            return libelle
        if not compte.dernier_releve:
            return _("Jamais relevé")
        age = maintenant - compte.dernier_releve
        if age > timedelta(hours=RELEVE_PERIME_H):
            heures = int(age.total_seconds() // 3600)
            return _("Aucun relevé depuis %s h", heures)
        return ""

    # ------------------------------------------------------------- rendu
    @staticmethod
    def _claude_usage_barre(valeur, couleur):
        """Une jauge en tableaux, la seule forme que tous les clients de
        courriel rendent. Bornée à 100 : un taux au-delà ne déborde pas."""
        largeur = max(0, min(100, round(valeur)))
        vide = 100 - largeur
        plein = (f'<td width="{largeur}%" style="background:{couleur};height:8px;'
                 f'font-size:0;line-height:0;">&nbsp;</td>') if largeur else ""
        reste = (f'<td width="{vide}%" style="background:#e5e7eb;height:8px;'
                 f'font-size:0;line-height:0;">&nbsp;</td>') if vide else ""
        return (
            '<table width="100%" cellpadding="0" cellspacing="0" '
            f'style="border-collapse:collapse;"><tr>{plein}{reste}</tr></table>'
        )

    def _render_claude_usage_section(self, user):
        """Le HTML de la section, ou '' quand il n'y a aucun compte à montrer."""
        self.ensure_one()
        if not self.include_claude_usage:
            return ""
        comptes = self._claude_usage_comptes(user)
        if not comptes:
            return ""

        maintenant = fields.Datetime.now()
        etats = dict(comptes._fields["etat"]._description_selection(self.env))
        couleurs = {"ok": "#15803d", "dormant": ORANGE,
                    "plafond": ROUGE, "muet": ROUGE}
        police = "font-family:Lexend,Helvetica,Arial,sans-serif;"
        cellule = f"padding:6px 10px;border-top:1px solid #eee;vertical-align:middle;{police}"

        cartes = []
        perimes = 0
        for compte in comptes:
            perime = self._claude_usage_perime(compte, maintenant)
            if perime:
                perimes += 1
                etat, couleur_etat = perime, ROUGE
            else:
                etat = etats.get(compte.etat, compte.etat)
                couleur_etat = couleurs.get(compte.etat, ANTHRACITE)

            # ⚠️ Tout passe par `_esc` ou `Markup.format` : une chaîne déjà
            # échappée ajoutée à un `Markup` est ré-échappée (`&amp;amp;`).
            entete = Markup(
                '<tr><td colspan="2" style="padding:8px 10px;background:#f8fafc;{}">'
                '<strong style="color:{};font-size:14px;">{}</strong>{}</td>'
                '<td style="padding:8px 10px;background:#f8fafc;text-align:right;'
                'color:{};font-weight:600;font-size:12px;{}">{}</td></tr>'
            ).format(
                police, ANTHRACITE, compte.name,
                Markup('<br/><span style="color:{};font-size:12px;">{}</span>').format(
                    GRIS, compte.courriel) if compte.courriel else "",
                couleur_etat, police, etat)

            lignes = []
            for fenetre in compte.window_ids.sorted(
                    lambda w: ORDRE_FENETRES.get(w.fenetre, 99)):
                seuil = (compte.seuil_session if fenetre.fenetre == "five_hour"
                         else compte.seuil_haut)
                couleur = ROUGE if fenetre.utilization >= seuil else BLEU
                if not fenetre.resets_at:
                    quand, detail = "", ""
                elif fenetre.resets_at <= maintenant:
                    # Le relevé est antérieur à la bascule : le chiffre ne vaut
                    # plus, et le dire vaut mieux que le montrer comme actuel.
                    quand, detail = _("bascule passée depuis le relevé"), ""
                else:
                    quand = _delai(fenetre.resets_at, maintenant)
                    detail = fenetre.bascule_montreal or ""
                lignes.append(Markup(
                    '<tr><td width="30%" style="{}color:{};">{}</td>'
                    '<td style="{}"><table width="100%" cellpadding="0" cellspacing="0">'
                    '<tr><td>{}</td><td width="48" style="padding-left:8px;text-align:right;'
                    'font-weight:600;color:{};{}">{}</td></tr></table></td>'
                    '<td width="34%" style="{}text-align:right;">'
                    '<span style="color:{};font-weight:600;">{}</span>{}</td></tr>'
                ).format(
                    cellule, ANTHRACITE,
                    LIBELLES_FENETRES.get(fenetre.fenetre, fenetre.fenetre),
                    cellule, Markup(self._claude_usage_barre(fenetre.utilization, couleur)),
                    couleur, police, _pourcent(fenetre.utilization),
                    cellule, ANTHRACITE, quand,
                    Markup('<br/><span style="color:{};font-size:11px;">{}</span>').format(
                        GRIS, detail) if detail else ""))
            if compte.credits_actifs:
                lignes.append(Markup(
                    '<tr><td width="30%" style="{}color:{};">{}</td>'
                    '<td style="{}"><table width="100%" cellpadding="0" cellspacing="0">'
                    '<tr><td>{}</td><td width="48" style="padding-left:8px;text-align:right;'
                    'font-weight:600;color:{};{}">{}</td></tr></table></td>'
                    '<td width="34%" style="{}"></td></tr>'
                ).format(
                    cellule, ANTHRACITE, _("Crédits en surplus"),
                    cellule, Markup(self._claude_usage_barre(
                        compte.credits_utilisation, BLEU)),
                    BLEU, police, _pourcent(compte.credits_utilisation), cellule))
            if not lignes:
                lignes.append(Markup(
                    '<tr><td colspan="3" style="{}color:{};">{}</td></tr>'
                ).format(cellule, GRIS, _("Aucune fenêtre mesurée")))

            cartes.append(
                '<table width="100%" cellpadding="0" cellspacing="0" '
                'style="border-collapse:collapse;border:1px solid #e5e7eb;'
                'margin:0 0 12px 0;font-size:13px;">'
                f'{entete}{"".join(lignes)}</table>'
            )

        # Deux phrases entières plutôt qu'un pluriel recollé.
        if perimes == 1:
            chapeau = _("Attention : un compte n'a pas de relevé à jour. Un "
                        "compteur figé n'est pas un compteur rassurant.")
        elif perimes:
            chapeau = _("Attention : %s comptes n'ont pas de relevé à jour. Un "
                        "compteur figé n'est pas un compteur rassurant.", perimes)
        else:
            chapeau = _("Ce qui a été consommé sur chaque abonnement au dernier "
                        "relevé, et le temps qui reste avant chaque remise à "
                        "zéro. Heures de Montréal.")
        couleur_chapeau = ROUGE if perimes else BLEU

        return (
            f'<h3 style="margin:0 0 4px 0;color:{ANTHRACITE};{police}">'
            f'{_esc(_("Consommation Claude"))}</h3>'
            f'<p style="margin:0 0 10px 0;color:{couleur_chapeau};font-size:13px;{police}">'
            f'{_esc(chapeau)}</p>'
            f'{"".join(cartes)}'
        )

    # ------------------------------------------------------------- conversations
    def _gen_followups(self, user):
        """Les conversations à suivre du DESTINATAIRE, lues en son nom.

        Trois paquets : à fermer (faites, ou endormies), qui t'attendent, et
        relancées par la passe de nuit sans réponse depuis.
        """
        Session = self.env["claude.chat.session"]
        # Un destinataire portail n'a pas Gen : rien à lire, et une lecture en
        # son nom lèverait (l'accès est réservé aux internes).
        if not user or user.share or not Session._closure_enabled():
            return {}
        # Sans plafond : les en-têtes comptent tout, comme la notification ;
        # c'est l'affichage qui se limite (GEN_SUIVI_PAR_PAQUET).
        sessions = Session.with_user(user).search(
            [("user_id", "=", user.id), ("origin", "in", ("web", "mobile"))]
            + Session._to_follow_domain(),
            order="list_date desc, id desc")
        paquets = {"fermer": [], "attend": [], "relance": []}
        for s in sessions:
            if s.closure_state in ("done", "idle"):
                paquets["fermer"].append(s)
            elif s.closure_state == "waiting":
                paquets["attend"].append(s)
            else:
                paquets["relance"].append(s)
        return {k: v for k, v in paquets.items() if v}

    def _render_gen_followups_section(self, user):
        """Le HTML de la section, ou '' quand rien n'attend.

        Ne lève jamais : une section qui tombe ferait tomber tout le courriel
        du destinataire, et ceux des suivants dans un envoi à la main.
        """
        self.ensure_one()
        if not self.include_gen_followups:
            return ""
        try:
            return self._render_gen_followups_html(user)
        except Exception:  # noqa: BLE001
            _logger.warning("Gen : section du digest impossible pour %s",
                            user.id if user else None, exc_info=True)
            return ""

    def _render_gen_followups_html(self, user):
        paquets = self._gen_followups(user)
        if not paquets:
            return ""
        police = "font-family:Lexend,Helvetica,Arial,sans-serif;"
        cellule = f"padding:6px 10px;border-top:1px solid #eee;vertical-align:top;{police}"
        base = self.env["ir.config_parameter"].sudo().get_param("web.base.url") or ""
        maintenant = fields.Datetime.now()
        titres = {
            "fermer": _("À fermer"),
            "attend": _("T'attendent"),
            "relance": _("Relancées, sans réponse"),
        }
        blocs = []
        for cle in ("fermer", "attend", "relance"):
            sessions = paquets.get(cle)
            if not sessions:
                continue
            lignes = []
            for s in sessions[:GEN_SUIVI_PAR_PAQUET]:
                jours = (maintenant - s.last_activity).days if s.last_activity else 0
                quand = _("aujourd'hui") if jours <= 0 else (
                    _("hier") if jours == 1 else _("il y a %s\u00a0j", jours))
                lien = f"{base}/odoo/action-bf_claude_chat.action_claude_chat?gen_session={s.id}"
                lignes.append(Markup(
                    '<tr><td style="{}"><a href="{}" style="color:{};text-decoration:none;'
                    'font-weight:600;">{}</a>{}</td>'
                    '<td width="22%" style="{}text-align:right;color:{};font-size:12px;">{}</td></tr>'
                ).format(
                    cellule, lien, ANTHRACITE, s.name,
                    Markup('<br/><span style="color:{};font-size:12px;">{}</span>').format(
                        GRIS, s.closure_reason) if s.closure_reason else "",
                    cellule, GRIS, quand))
            reste = len(sessions) - GEN_SUIVI_PAR_PAQUET
            if reste > 0:
                lignes.append(Markup(
                    '<tr><td colspan="2" style="{}color:{};font-size:12px;">{}</td></tr>'
                ).format(cellule, GRIS, _("et %s autre(s)", reste)))
            blocs.append(
                # Anthracite et non bleu : le bleu de marque sur blanc est trop
                # pâle pour un titre de 13 px.
                f'<p style="margin:10px 0 4px 0;color:{ANTHRACITE};font-weight:700;'
                f'font-size:13px;{police}">{_esc(titres[cle])} ({len(sessions)})</p>'
                '<table width="100%" cellpadding="0" cellspacing="0" '
                'style="border-collapse:collapse;border:1px solid #e5e7eb;font-size:13px;">'
                f'{"".join(lignes)}</table>'
            )
        return (
            f'<h3 style="margin:0 0 4px 0;color:{ANTHRACITE};{police}">'
            f'{_esc(_("Conversations Gen à suivre"))}</h3>'
            f'<p style="margin:0 0 4px 0;color:{GRIS};font-size:13px;{police}">'
            f'{_esc(_("Ce que Gen tient pour fait, ce qui attend ton geste, et ce que la passe de nuit a relancé."))}</p>'
            f'{"".join(blocs)}'
        )

    @staticmethod
    def _splice_claude_usage_section(html, section):
        """Glisser la section devant le marqueur, dans sa propre ligne.

        ⚠️ Le marqueur « <!-- Divider --> » se trouve ENTRE deux <tr> de la
        table d'enveloppe. Une section nue insérée là est sortie de la table par
        tout analyseur HTML5 et s'affiche au-dessus de la carte.
        """
        if not section or "<!-- Divider -->" not in html:
            return html
        bloc = f'<tr><td style="padding:0 24px 24px 24px;">{section}</td></tr>'
        return html.replace("<!-- Divider -->", bloc + "<!-- Divider -->", 1)

    # ------------------------------------------------------------- notification
    @api.model
    def _cron_send_daily_digests(self):
        """La notification du jour part avec le courriel du cron,
        jamais avec un « envoyer un essai » ni un envoi à la main."""
        return super(DailyDigestConfig, self.with_context(
            bf_gen_push=True))._cron_send_daily_digests()

    def _send_digest_to(self, user):
        res = super()._send_digest_to(user)
        # La case du digest vaut pour la notification aussi : décochée, ni
        # section ni poussée. Une par jour, même avec plusieurs digests.
        if self.env.context.get("bf_gen_push") and self.include_gen_followups:
            try:
                self.env["claude.chat.session"]._push_closure_summary(user)
            except Exception:  # noqa: BLE001 — le courriel est parti, c'est l'essentiel
                _logger.warning("Gen : notification du jour impossible pour %s",
                                user.id, exc_info=True)
        return res

    def _generate_html(self, data, user):
        html = super()._generate_html(data, user)
        html = self._splice_claude_usage_section(
            html, self._render_gen_followups_section(user))
        return self._splice_claude_usage_section(
            html, self._render_claude_usage_section(user))
