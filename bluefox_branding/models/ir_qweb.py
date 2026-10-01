from odoo import api, models


class IrQweb(models.AbstractModel):
    _inherit = 'ir.qweb'

    @api.model
    def _render(self, template, values=None, **options):
        """Lire la société d'une mise en page de courriel dans la langue du rendu.

        La mise en page lit `brand_email_tagline` et `brand_email_footer_html`,
        traduisibles. Odoo rend la mise en page dans la langue du destinataire
        (`ir.qweb` avec `lang`), mais lui passe la société telle que l'appelant
        l'a lue : par `mail.template.send_mail`, un courriel anglais envoyé par
        une personne en français portait le slogan et le pied français. Les deux
        chemins ne disent pas la langue au même endroit : `send_mail` la met dans
        le contexte, les avis (`_notify_by_email_render_layout`, donc le
        composeur) la passent en option `lang`. Une mise en page de courriel se
        reconnaît à `message` et `company`.
        """
        values = values or {}
        company = values.get('company')
        lang = options.get('lang') or self.env.lang
        if (lang and 'message' in values and isinstance(company, models.BaseModel)
                and company._name == 'res.company' and company.env.lang != lang):
            values = dict(values, company=company.with_context(lang=lang))
        return super()._render(template, values, **options)
