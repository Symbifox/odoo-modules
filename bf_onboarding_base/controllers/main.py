"""Le logo d'une société, lisible par quelqu'un qui n'a pas de compte.

`/web/image/res.company/<id>/<champ>` ne sert la vraie image qu'aux sociétés que
l'usager ANONYME a le droit de lire, c'est à dire la seule société du site web.
Pour toute autre, Odoo avale l'AccessError et rend son image grise de
remplacement avec un **HTTP 200** : ni code d'erreur, ni trace dans les
journaux. Sur une base multi-société, le logo d'une société secondaire
disparaissait donc de tout courriel brandé et de toute page publique, alors que
les PDF, rendus côté serveur avec les droits d'un usager interne, restaient
corrects. C'est ce décalage entre deux sorties du même champ qui met sur la
piste.

Un logo est de la marque publique par nature, et Odoo sert déjà n'importe quel
logo de société en sudo sur `/logo.png?company=<id>`. Cette route fait la même
chose, en sachant résoudre les champs de marque des modules maison, que
`/logo.png` ignore.
"""

from odoo.http import Controller, request, route


class BrandLogo(Controller):

    # 🔴 C'est la VARIANTE qui voyage dans l'URL, jamais un nom de champ : la
    # route lit en sudo, et sans cette liste blanche elle deviendrait une
    # lecture arbitraire de `res.company`.
    #
    # ⚠️ Les quatre ordres ci-dessous sont ceux qu'écrivaient les gabarits, et
    # ils ne sont pas interchangeables. `own` a l'air d'un doublon de `logo`
    # tant qu'aucune société n'a de logo de marque sans logo ordinaire ; le
    # jour où ça arrive, les fondre changerait l'image servie.
    LOGO_VARIANTS = {
        'logo': ('logo',),
        'brand': ('report_brand_logo', 'logo'),
        'own': ('logo', 'report_brand_logo'),
        'meeting': ('meeting_logo', 'logo'),
    }

    # Le champ source est un `image_1920` : il pesait 1,2 Mo sur une des
    # sociétés, ce qui n'a pas sa place dans un courriel. 120 px de haut
    # couvrent les 40 px des bandeaux de courriel en triple densité.
    LOGO_HEIGHT = 120

    @route(['/brand/logo/<int:company_id>',
            '/brand/logo/<int:company_id>/<string:variant>'],
           type='http', auth='public', methods=['GET'], csrf=False)
    def brand_company_logo(self, company_id, variant='logo', **kw):
        champs = self.LOGO_VARIANTS.get(variant)
        if champs is None:
            return request.not_found()
        company = request.env['res.company'].sudo().browse(company_id).exists()
        if not company:
            return request.not_found()
        # `meeting_logo` n'existe que si bf_meeting est installé : la garde sur
        # `_fields` évite de faire dépendre le socle d'un module qui dépend de
        # lui.
        champ = next(
            (f for f in champs if f in company._fields and company[f]),
            'logo',
        )
        stream = request.env['ir.binary']._get_image_stream_from(
            company, champ, height=self.LOGO_HEIGHT, width=0)
        return stream.get_response()
