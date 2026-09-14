# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Section « Mises à jour du parc » du digest quotidien.

Même patron d'injection que les autres sections du digest : bâtir le HTML,
puis l'insérer devant le marqueur `<!-- Divider -->` du gabarit de base.

⚠️ Cette section-ci a une particularité : **le silence a deux sens et un seul
est bon**. Se taire parce que rien n'est en
retard est juste. Se taire parce que les machines ne parlent plus reviendrait à
dire « rien à signaler » quand la vraie phrase est « personne ne mesure ». Un
système muet fait donc TOUJOURS paraître la section.

C'est la règle du module, appliquée à sa propre surface : un relevé absent est
une alerte, jamais un silence.

🔴 La section se lit AU NOM DU DESTINATAIRE, pas en `sudo`. Le digest part à
chaque personne de `user_ids`, sans condition d'accès : une lecture en `sudo`
donnait les noms et l'état des machines de tous les clients à n'importe quel
destinataire, alors que la tuile de l'accueil se cache à qui n'a pas l'accès.
Relevé par une relecture adverse avant publication (18.0.1.1.0). Sans accès à
l'hébergement, pas de section ; avec, les règles d'enregistrement bornent la
liste aux clients de la personne.
"""

from markupsafe import Markup, escape as _esc

from odoo import _, fields, models

# Combien de systèmes la section nomme au plus. Le reste est compté. Un
# courriel n'est pas une file de travail : il dit s'il vaut la peine d'ouvrir
# l'écran.
MAX_SYSTEMES = 8

# Les états qui méritent qu'on écrive, dans l'ordre de gravité du module
# (`STATE_SEVERITY` de `bf_patch_system`) : un muet, puis la sécurité, puis le
# redémarrage, puis le compte inconnu.
ETATS_PARLANTS = ("stale", "security", "reboot", "blind")

HOSTING_USER = "hosting_management.group_hosting_user"


def _pluriel(nombre, singulier, pluriel):
    """« 1 système », pas « 1 systèmes ». Le digest part chaque matin."""
    return f"{nombre} {singulier if abs(nombre) == 1 else pluriel}"


class DailyDigestConfig(models.Model):
    _inherit = "daily.digest.config"

    include_hosting_patch = fields.Boolean(
        string="Inclure l'état de mise à jour du parc", default=True,
    )

    # ------------------------------------------------------------- collecte
    def _hosting_patch_systemes(self, user):
        """Les systèmes qui méritent une phrase, groupés par état, tels que
        le DESTINATAIRE a le droit de les voir.

        ⚠️ `with_user(user)` et non `sudo()` : ce sont les règles
        d'enregistrement de `bf_hosting_patch` qui bornent la liste aux clients
        de la personne. Le digest part d'une tâche planifiée, donc d'un
        superutilisateur ; lire sans changer d'usager reviendrait au `sudo`.
        """
        if not user or not user.has_group(HOSTING_USER):
            return {}
        Systeme = self.env["bf.patch.system"].with_user(user)
        groupes = {}
        for etat in ETATS_PARLANTS:
            systemes = Systeme.search([("patch_state", "=", etat)])
            if systemes:
                groupes[etat] = systemes
        return groupes

    # ------------------------------------------------------------- rendu
    def _render_hosting_patch_section(self, user):
        """Le HTML de la section, ou '' quand il n'y a rien à dire."""
        self.ensure_one()
        if not self.include_hosting_patch:
            return ""

        groupes = self._hosting_patch_systemes(user)
        if not groupes:
            return ""

        # ⚠️ Les couleurs de marque viennent de `bluefox_branding`, qui n'est
        # pas une dépendance : la section doit rester lisible sans lui.
        anthracite = "#2E3132"
        bleu = "#29ABE2"
        rouge = "#C0392B"
        orange = "#D68910"

        libelles = {
            "stale": (_("muet, aucun relevé récent"), rouge),
            "security": (_("correctif de sécurité en attente"), rouge),
            "blind": (_("compte de paquets inconnu"), orange),
            "reboot": (_("redémarrage requis"), orange),
        }

        lignes = []
        for etat, systemes in groupes.items():
            libelle, couleur = libelles[etat]
            nommes = systemes[:MAX_SYSTEMES]
            reste = len(systemes) - len(nommes)
            # ⚠️ Tout en `Markup` : ajouter une chaîne déjà échappée à un
            # `Markup` la ré-échappe, et les noms sortaient en `&amp;amp;` dès
            # qu'il y avait plus de MAX_SYSTEMES systèmes à nommer.
            noms = Markup(", ").join(
                Markup("{} / {}").format(s.endpoint_id.name, s.name)
                for s in nommes
            )
            if reste:
                noms += _(" et %s de plus", reste)
            lignes.append(
                f'<tr>'
                f'<td style="padding:6px 10px;border-bottom:1px solid #eee;'
                f'color:{couleur};font-weight:600;white-space:nowrap;">'
                f'{_esc(_pluriel(len(systemes), _("système"), _("systèmes")))}</td>'
                f'<td style="padding:6px 10px;border-bottom:1px solid #eee;'
                f'color:{couleur};">{_esc(libelle)}</td>'
                f'<td style="padding:6px 10px;border-bottom:1px solid #eee;'
                f'color:{anthracite};">{noms}</td>'
                f'</tr>'
            )

        muets = len(groupes.get("stale", []))
        chapeau = _("Ce que les machines du parc rapportent.")
        # La phrase qui distingue « rien à signaler » de « personne ne
        # mesure ». Elle est la raison d'être de la section. Deux phrases
        # entières plutôt qu'un pluriel recollé : l'assemblage donnait
        # « 1 un système ne rapporte plus ».
        if muets == 1:
            chapeau = _("Attention : un système ne rapporte plus. Un relevé "
                        "absent n'est pas une bonne nouvelle.")
        elif muets:
            chapeau = _("Attention : %s systèmes ne rapportent plus. Un relevé "
                        "absent n'est pas une bonne nouvelle.", muets)

        return (
            f'<h3 style="margin:0 0 4px 0;color:{anthracite};'
            f'font-family:Lexend,Helvetica,Arial,sans-serif;">'
            f'{_esc(_("Mises à jour du parc"))}</h3>'
            f'<p style="margin:0 0 10px 0;color:{bleu};font-size:13px;">'
            f'{_esc(chapeau)}</p>'
            f'<table style="width:100%;border-collapse:collapse;font-size:13px;'
            f'font-family:Lexend,Helvetica,Arial,sans-serif;">'
            f'<tbody>{"".join(lignes)}</tbody></table>'
        )

    def _generate_html(self, data, user):
        html = super()._generate_html(data, user)
        section = self._render_hosting_patch_section(user)
        if section and "<!-- Divider -->" in html:
            # ⚠️ Le marqueur se trouve ENTRE deux <tr> de la table
            # d'enveloppe. Une section nue insérée là est sortie de la table
            # par tout analyseur HTML5 et s'affiche au-dessus de la carte :
            # elle doit porter sa propre ligne.
            bloc = f'<tr><td style="padding:0 24px 24px 24px;">{section}</td></tr>'
            html = html.replace("<!-- Divider -->", bloc + "<!-- Divider -->", 1)
        return html
