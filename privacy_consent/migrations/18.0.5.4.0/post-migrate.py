"""Les courriels de consentement ne mènent plus au centre de préférences.

Les six gabarits portaient un lien vers `/my/privacy/preferences`, une page
`auth="user"` : pour une personne sans session, c'est l'écran de connexion du
portail. Or ces courriels vont surtout à des personnes de l'extérieur, sans
compte, et toutes les réponses reçues jusqu'ici sont passées par le lien public
à jeton du consentement (`/privacy/consent/<id>/<jeton>`), qui s'ouvre sans
connexion et offre la réponse, le retrait et le renouvellement.

Décision du 2026-10-02 :

* demande, rappels 1 et 2 : le lien « Preferences » sous le contenu disparaît,
  le bouton principal ouvre déjà la page à jeton ;
* avis d'expiration : le second bouton « Gérer mes préférences » disparaît, le
  premier ouvre déjà la page à jeton ;
* confirmations (octroi, renouvellement) : le bouton mène à la page à jeton du
  consentement, « Gérer mon consentement », et le texte ne promet plus un
  centre de préférences.

Au passage, la branche anglaise de quatre gabarits affichait un repli en
français (« Aucune expiration », « Bientôt », « Aucune description
disponible. ») quand la valeur manquait.

Les gabarits sont en `noupdate` : la mise à jour ne réécrit pas les corps
stockés. On retouche donc chaque langue stockée avec le même outil que celui
qui a retouché la source (`retoucher`). Une retouche introuvable (corps refait
à la main) est journalisée et laissée telle quelle ; un lien vers les
préférences qui resterait après les retouches connues est repointé vers la
page à jeton, pour qu'aucun courriel ne mène plus à la connexion. Rejouée, la
migration ne trouve plus rien à faire.
"""
import json
import logging
import re

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

GABARITS = (
    "mail_template_consent_request",
    "mail_template_consent_expiring",
    "mail_template_consent_reminder_1",
    "mail_template_consent_reminder_2",
    "mail_template_consent_renewal_confirmation",
    "mail_template_consent_granted_confirmation",
)
PREFERENCES = "/my/privacy/preferences"
JETON = "/privacy/consent/{{ object.id }}/{{ object.access_token }}"
# Un `href` simple n'interpole pas `{{ object.id }}` : il devient `t-attf-href`.
HREF_SIMPLE = re.compile(r'(?<![-\w])href="([^"]*)' + re.escape(PREFERENCES) + '"')


def _vers_jeton(corps):
    corps = HREF_SIMPLE.sub(lambda m: 't-attf-href="%s%s"' % (m.group(1), JETON), corps)
    return corps.replace(PREFERENCES + '"', JETON + '"')


_LIEN = r'<a\b[^>]*%s"[^>]*>[^<]*</a>' % re.escape(PREFERENCES)
# Le lien sous le contenu (demande, rappels) : tout le paragraphe part avec lui.
PIED = re.compile(r'\n?<p\b[^>]*>\s*' + _LIEN + r'\s*</p>')
# Le second bouton de l'avis d'expiration, avec la cellule qui l'espaçait du premier.
SECOND_BOUTON = re.compile(
    r'\s*<td width="12">[^<]*</td>\s*<td align="left">\s*' + _LIEN + r'\s*</td>')

# (gabarit, motif, remplacement). Les motifs tolèrent le retour à la ligne
# entre deux mots, qui varie d'une langue stockée à l'autre.
TEXTES = (
    ("mail_template_consent_request",
     r"or through your preferences portal\.", "or from the link in this email."),
    ("mail_template_consent_request",
     r"ou via votre portail de préférences\.", "ou à partir du lien de ce courriel."),
    ("mail_template_consent_renewal_confirmation",
     r"You can manage your preferences at any time\.",
     "You can view or withdraw this consent at any time using the link below."),
    ("mail_template_consent_renewal_confirmation",
     r"Vous pouvez gérer vos préférences à tout moment\.",
     "Vous pouvez consulter ou retirer ce consentement à tout moment à partir du lien ci-dessous."),
    ("mail_template_consent_renewal_confirmation",
     r"(>\s*)View my preferences(\s*</a>)", r"\1Manage my consent\2"),
    ("mail_template_consent_renewal_confirmation",
     r"(>\s*)Voir mes préférences(\s*</a>)", r"\1Gérer mon consentement\2"),
    ("mail_template_consent_granted_confirmation",
     r"at any time from our preferences centre\.", "at any time using the link below."),
    ("mail_template_consent_granted_confirmation",
     r"(à tout moment\s+)via notre centre de préférences\.", r"\1à partir du lien ci-dessous."),
    ("mail_template_consent_granted_confirmation",
     r"(>\s*)Manage my preferences(\s*</a>)", r"\1Manage my consent\2"),
    ("mail_template_consent_granted_confirmation",
     r"(>\s*)Gérer mes préférences(\s*</a>)", r"\1Gérer mon consentement\2"),
)

# Replis en français dans la branche anglaise (avant le premier `<t t-else="">`).
REPLIS_EN = (
    ("'Aucune description disponible.'", "'No description available.'"),
    ("'Aucune expiration'", "'No expiry'"),
    ("'Bientôt'", "'Soon'"),
)
SINON = '<t t-else="">'


def retoucher(xmlid, corps):
    """(nouveau corps, retouches introuvables). Rien à faire : le corps revient tel quel."""
    corps = corps or ""
    # Un corps sans le lien (refait à la main, ou déjà retouché) n'a rien à signaler.
    standard = PREFERENCES in corps
    manques = []
    if xmlid in ("mail_template_consent_request",
                 "mail_template_consent_reminder_1",
                 "mail_template_consent_reminder_2"):
        corps, n = PIED.subn("", corps)
    elif xmlid == "mail_template_consent_expiring":
        corps, n = SECOND_BOUTON.subn("", corps)
    else:
        n = corps.count(PREFERENCES + '"')
        corps = _vers_jeton(corps)
    if not n and PREFERENCES in corps:
        manques.append(PREFERENCES)
    for gabarit, motif, remplacement in TEXTES:
        if gabarit == xmlid:
            corps, n = re.subn(motif, remplacement, corps)
            if not n and re.sub(r"\\[12]", "", remplacement) not in corps:
                manques.append(motif)
    anglais, sinon, reste = corps.partition(SINON)
    for avant, apres in REPLIS_EN:
        anglais = anglais.replace(avant, apres)
    corps = anglais + sinon + reste
    # Filet : un lien vers les préférences resté dans un corps retouché à la main
    # mène à la page à jeton plutôt qu'à la connexion.
    corps = _vers_jeton(corps)
    return corps, (manques if standard else [])


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    for xmlid in GABARITS:
        template = env.ref(f"privacy_consent.{xmlid}", raise_if_not_found=False)
        if not template:
            continue
        cr.execute("SELECT body_html FROM mail_template WHERE id = %s", [template.id])
        valeurs = cr.fetchone()[0] or {}
        nouvelles = {}
        for lang, corps in valeurs.items():
            nouveau, manques = retoucher(xmlid, corps)
            if manques:
                _logger.warning("privacy_consent 18.0.5.4.0 : %s (%s), introuvable : %s",
                                xmlid, lang, " ; ".join(manques))
            if nouveau != corps:
                nouvelles[lang] = nouveau
        if not nouvelles:
            continue
        cr.execute("UPDATE mail_template SET body_html = %s::jsonb WHERE id = %s",
                   [json.dumps(dict(valeurs, **nouvelles)), template.id])
        template.invalidate_recordset(["body_html"])
        _logger.info("privacy_consent 18.0.5.4.0 : %s retouché (%s)",
                     xmlid, ", ".join(sorted(nouvelles)))
