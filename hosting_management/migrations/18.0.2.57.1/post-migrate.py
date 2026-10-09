"""Les courriels de ce module ne retombent plus sur Blue Fox.

Les gabarits sont corrigés EN BASE, dans toutes les langues : une partie est
`noupdate`, un -u ne les recharge donc pas, et les recharger de force effacerait
ce que le locataire y a personnalisé. Seuls les littéraux Blue Fox sont remplacés
(nom, site, logo, liens légaux, slogan) ; le reste du gabarit n'est pas touché.
Rejouable : un gabarit déjà corrigé ne correspond plus à aucune règle.
"""
import json
import logging
import re

_logger = logging.getLogger(__name__)
MODULE = "hosting_management"

# >>> RÈGLES (recopiées telles quelles dans chaque migration)
BF_LOGO = r"https://(?:www\.)?bluefoxconsultant\.com/web/image/website/1/logo(?:/Blue%20Fox\?unique=\w+)?"
BF_SITE = r"https://(?:www\.)?bluefoxconsultant\.com"
TAGLINE = "Solutions éthiques et souveraines pour vos données."
SEP = '<span style="color:#9CA3AF;"> | </span>'

# Une expression de société « (a.b.company_id and a.b.company_id.appointment_brand_x) ».
C = r"(?P<c>[\w.]+)"


REGLES = [
    # --- Rendez-vous et sondages : champs appointment_brand_* ---------------------------
    (re.compile(r"\(" + C + r" and (?P=c)\.appointment_brand_name\) or '(?:Blue Fox)'"),
     r"(\g<c> and (\g<c>.appointment_brand_name or \g<c>.name)) or ''"),
    (re.compile(r"\(" + C + r" and (?P=c)\.appointment_brand_website_url\) or '" + BF_SITE + "'"),
     r"(\g<c> and (\g<c>.appointment_brand_website_url or \g<c>.website)) or object.get_base_url()"),
    (re.compile(r"\(" + C + r" and (?P=c)\.appointment_brand_logo_url\) or '" + BF_LOGO + "'"),
     r"(\g<c> and \g<c>.appointment_brand_logo_url) or '%s/logo.png?company=%s' % (object.get_base_url(), \g<c>.id or '')"),
    (re.compile(r"\(" + C + r" and (?P=c)\.appointment_brand_tagline\) or '" + re.escape(TAGLINE) + "'"),
     r"(\g<c> and \g<c>.appointment_brand_tagline) or ''"),
    (re.compile(r'<a t-attf-href="\{\{ \(' + C + r" and (?P=c)\.appointment_brand_(?P<f>privacy|terms)_url\) or '[^']*' \}\}\""),
     r'<a t-if="\g<c> and \g<c>.appointment_brand_\g<f>_url" t-attf-href="{{ \g<c>.appointment_brand_\g<f>_url }}"'),
    # --- L'adresse de soutien de Blue Fox en repli ----------------------------------------
    (re.compile(r"\(" + C + r" and (?P=c)\.appointment_brand_support_email\) or '[\w.+-]+@bluefoxconsultant\.com'"),
     r"(\g<c> and \g<c>.appointment_brand_support_email) or ''"),
    (re.compile(r"company\.email or '[\w.+-]+@bluefoxconsultant\.com'"), "company.email or ''"),
    (re.compile(r'<a t-else="" href="mailto:[\w.+-]+@bluefoxconsultant\.com"'), '<a t-elif="False" href=""'),
    (re.compile(r'href="mailto:[\w.+-]+@bluefoxconsultant\.com"'),
     't-attf-href="mailto:{{ company.email if company else ' + "''" + ' }}"'),
    (re.compile(r">[\w.+-]+@bluefoxconsultant\.com<"), ">" + """<t t-out="company.email if company else ''"/>""" + "<"),
    # --- Gabarits qui lisent la société : site, nom, liens légaux ----------------------
    (re.compile(r"<a t-attf-href=\"\{\{ company\.website or '" + BF_SITE + r"' \}\}/r/politique-de-confidentialite\""),
     r"""<a t-if="company and 'brand_privacy_url' in company and company.brand_privacy_url" t-attf-href="{{ company.brand_privacy_url }}""" + '"'),
    (re.compile(r"<a t-attf-href=\"\{\{ company\.website or '" + BF_SITE + r"' \}\}/r/termes-et-conditions\""),
     r"""<a t-if="company and 'brand_terms_url' in company and company.brand_terms_url" t-attf-href="{{ company.brand_terms_url }}""" + '"'),
    (re.compile(r"company\.website or '" + BF_SITE + "'"), "company.website or object.get_base_url()"),
    (re.compile(r"company\.name or '(?:Blue Fox)'"), "company.name"),
    (re.compile(r"\(company and company\.name\) or \\?'Blue Fox\\?'"), "(company and company.name) or ''"),
    # --- Matrice de connaissances : marque écrite en dur -------------------------------
    (re.compile(r'<img src="' + BF_LOGO + r'" alt="Blue Fox"'),
     r"""<img t-attf-src="{{ object.get_base_url() }}/logo.png?company={{ company.id if company else '' }}" t-att-alt="company.name if company else ''""" + '"'),
    (re.compile(r'<a href="' + BF_SITE + r'/r/politique-de-confidentialite"'),
     r"""<a t-if="company and 'brand_privacy_url' in company and company.brand_privacy_url" t-attf-href="{{ company.brand_privacy_url }}""" + '"'),
    (re.compile(r'<a href="' + BF_SITE + r'/r/termes-et-conditions"'),
     r"""<a t-if="company and 'brand_terms_url' in company and company.brand_terms_url" t-attf-href="{{ company.brand_terms_url }}""" + '"'),
    # --- Traductions qui ont figé le nom en texte : « L'équipe Blue Fox », « … par Blue Fox »
    (re.compile(r"L'(é|&#233;)quipe Blue Fox"), r"""L'\1quipe <t t-out="company.name if company else ''"/>"""),
    (re.compile(r" par Blue Fox(?=\s*<)"), r""" par <t t-out="company.name if company else ''"/>"""),
    (re.compile(r">Blue Fox Inc\.?<"), ">" + """<t t-out="company.name if company else ''"/>""" + "<"),
    (re.compile(r'(?<![\w-])alt="Blue Fox"'), 't-att-alt="company.name if company else ' + "''" + '"'),
    (re.compile(r'<a href="' + BF_SITE + r'"(?P<reste>[^>]*)>bluefoxconsultant\.com</a>'),
     r"""<a t-if="company and company.website" t-attf-href="{{ company.website }}"\g<reste>><t t-out="company.website"/></a>"""),
    (re.compile(r'<a href="' + BF_SITE + r'"'),
     r"""<a t-attf-href="{{ (company and company.website) or object.get_base_url() }}""" + '"'),
]

# Le séparateur « | » entre deux liens légaux : visible seulement si les deux le sont.
SEP_RE = re.compile(
    r'(?P<a1><a t-if="(?P<cond>[^"]*privacy_url)"[^>]*>.*?</a>\s*)' + re.escape(SEP))


SUJET_RE = re.compile(r" - Blue Fox\s*$")


def regle_sujet(texte):
    """L'objet d'un courriel : le nom figé par une traduction redevient celui de la société."""
    texte, n = SUJET_RE.subn(" - {{ object.company_id.name }}", texte)
    texte, k = re.subn(r" chez Blue Fox(?= !)", "", texte)
    return texte, n + k


def regle(texte):
    """Applique les règles ; rend (texte, nombre de remplacements)."""
    total = 0
    for motif, rempl in REGLES:
        texte, n = motif.subn(rempl, texte)
        total += n

    def sep(m):
        cond2 = m.group("cond").replace("privacy_url", "terms_url")
        return m.group("a1") + SEP.replace("<span ", f'<span t-if="{m.group("cond")} and {cond2}" ', 1)

    texte, n = SEP_RE.subn(sep, texte)
    total += n
    # Le slogan de Blue Fox écrit en dur dans un gabarit, seul sur sa ligne.
    texte, n = re.subn(r"(?m)^(\s*)" + re.escape(TAGLINE) + r"\s*$",
                       r"""\1<t t-if="company and 'brand_email_tagline' in company" t-out="company.brand_email_tagline or ''"/>""",
                       texte)
    total += n
    return texte, total


def migrate(cr, version):
    cr.execute("""
        SELECT t.id, t.body_html, t.subject FROM mail_template t
        JOIN ir_model_data d ON d.model = 'mail.template' AND d.res_id = t.id
        WHERE d.module = %s""", [MODULE])
    corriges = 0
    for tid, body, subject in cr.fetchall():
        sets, vals = [], []
        for col, valeur in (("body_html", body), ("subject", subject)):
            if not isinstance(valeur, dict):
                continue
            nouveau, n = {}, 0
            for lang, texte in valeur.items():
                if isinstance(texte, str):
                    texte, k = regle(texte)
                    n += k
                    if col == "subject":
                        texte, k = regle_sujet(texte)
                        n += k
                nouveau[lang] = texte
            if n:
                sets.append(f"{col} = %s::jsonb")
                vals.append(json.dumps(nouveau))
        if sets:
            cr.execute(f"UPDATE mail_template SET {', '.join(sets)} WHERE id = %s", vals + [tid])
            corriges += 1
    _logger.info("%s : %s gabarit(s) sans repli Blue Fox", MODULE, corriges)
