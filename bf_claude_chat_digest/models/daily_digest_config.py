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

from datetime import timedelta

from markupsafe import Markup, escape as _esc

from odoo import _, fields, models
from odoo.addons.bf_claude_chat.models.claude_account import FENETRES

ADMIN = "base.group_system"

# La sonde verse aux deux heures. Trois passes manquées, et le relevé ne dit
# plus rien du présent.
RELEVE_PERIME_H = 6

ANTHRACITE = "#2E3132"
BLEU = "#29ABE2"
ROUGE = "#C0392B"
ORANGE = "#D68910"
GRIS = "#6b7280"

ORDRE_FENETRES = {cle: rang for rang, (cle, _libelle) in enumerate(FENETRES)}
LIBELLES_FENETRES = dict(FENETRES)


def _pourcent(valeur):
    # Espace insécable avant le signe, comme le veut l'usage en français.
    return f"{valeur:.0f} %"


class DailyDigestConfig(models.Model):
    _inherit = "daily.digest.config"

    include_claude_usage = fields.Boolean(
        string="Inclure la consommation Claude", default=True,
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
        couleurs = {"ok": ANTHRACITE, "dormant": ORANGE,
                    "plafond": ROUGE, "muet": ROUGE}
        cellule = "padding:6px 10px;border-bottom:1px solid #eee;vertical-align:top;"

        lignes = []
        perimes = 0
        for compte in comptes:
            perime = self._claude_usage_perime(compte, maintenant)
            if perime:
                perimes += 1
                etat, couleur = perime, ROUGE
            else:
                etat = etats.get(compte.etat, compte.etat)
                couleur = couleurs.get(compte.etat, ANTHRACITE)

            # ⚠️ Tout en `Markup` : une chaîne déjà échappée ajoutée à un
            # `Markup` est ré-échappée et sort en `&amp;amp;`.
            fenetres = []
            for fenetre in compte.window_ids.sorted(
                    lambda w: ORDRE_FENETRES.get(w.fenetre, 99)):
                if not fenetre.resets_at:
                    bascule = ""
                elif fenetre.resets_at <= maintenant:
                    # Le relevé est antérieur à la bascule : le chiffre ne vaut
                    # plus, et le taire serait moins faux que de le montrer nu.
                    bascule = _("bascule passée depuis le relevé")
                else:
                    bascule = _("bascule %s", fenetre.bascule_montreal)
                fenetres.append(Markup(
                    '<span style="color:{};">{}</span> <strong>{}</strong>'
                    '<span style="color:{};">{}</span>').format(
                    GRIS, LIBELLES_FENETRES.get(fenetre.fenetre, fenetre.fenetre),
                    _pourcent(fenetre.utilization),
                    GRIS, f" · {bascule}" if bascule else ""))
            if compte.credits_actifs:
                fenetres.append(Markup(
                    '<span style="color:{};">{}</span> <strong>{}</strong>').format(
                    GRIS, _("Crédits en surplus"),
                    _pourcent(compte.credits_utilisation)))
            detail = Markup("<br/>").join(fenetres) if fenetres else Markup(
                '<span style="color:{};">{}</span>').format(
                GRIS, _("Aucune fenêtre mesurée"))

            lignes.append(
                f"<tr>"
                f'<td style="{cellule}color:{ANTHRACITE};font-weight:600;'
                f'white-space:nowrap;">{_esc(compte.name)}</td>'
                f'<td style="{cellule}color:{couleur};">{_esc(etat)}</td>'
                f'<td style="{cellule}color:{ANTHRACITE};">{detail}</td>'
                f"</tr>"
            )

        # Deux phrases entières plutôt qu'un pluriel recollé.
        if perimes == 1:
            chapeau = _("Attention : un compte n'a pas de relevé à jour. Un "
                        "compteur figé n'est pas un compteur rassurant.")
        elif perimes:
            chapeau = _("Attention : %s comptes n'ont pas de relevé à jour. Un "
                        "compteur figé n'est pas un compteur rassurant.", perimes)
        else:
            chapeau = _("Ce qui a été consommé sur chaque abonnement, au "
                        "dernier relevé de la sonde. Bascules en heure de "
                        "Montréal.")
        couleur_chapeau = ROUGE if perimes else BLEU

        return (
            f'<h3 style="margin:0 0 4px 0;color:{ANTHRACITE};'
            f'font-family:Lexend,Helvetica,Arial,sans-serif;">'
            f'{_esc(_("Consommation Claude"))}</h3>'
            f'<p style="margin:0 0 10px 0;color:{couleur_chapeau};font-size:13px;">'
            f'{_esc(chapeau)}</p>'
            f'<table style="width:100%;border-collapse:collapse;font-size:13px;'
            f'font-family:Lexend,Helvetica,Arial,sans-serif;">'
            f'<tbody>{"".join(lignes)}</tbody></table>'
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

    def _generate_html(self, data, user):
        html = super()._generate_html(data, user)
        return self._splice_claude_usage_section(
            html, self._render_claude_usage_section(user))
