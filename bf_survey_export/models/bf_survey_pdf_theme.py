import base64
import binascii
import re

from markupsafe import Markup

from odoo import api, fields, models
from odoo.exceptions import ValidationError

# Every value below ends up inside a <style> block of the PDF, where HTML
# escaping does not apply: a colour or a font name is therefore accepted only
# in a shape that cannot close the block nor open a rule of its own.
_HEX = re.compile(r"#[0-9A-Fa-f]{6}")
# Comma-separated names, each bare or between single quotes (used whole,
# with fullmatch: a trailing newline or a lone quote does not pass).
_NAME = r"(?:'[A-Za-z0-9 \-]+'|[A-Za-z0-9\-]+(?: [A-Za-z0-9\-]+)*)"
_FAMILY = re.compile(rf" *{_NAME}(?: *, *{_NAME}){{0,5}} *")
# A font file is embedded in every page of the PDF: keep it reasonable.
_FONT_MAX_BYTES = 1024 * 1024
# TrueType, OpenType, Apple TrueType, WOFF.
_FONT_MAGIC = (b"\x00\x01\x00\x00", b"OTTO", b"true", b"wOFF")

DEFAULT_PRIMARY = "#1F3864"
DEFAULT_ACCENT = "#2E75B6"
DEFAULT_TEXT = "#2D3031"
DEFAULT_FAMILY = "'Liberation Sans', Arial, sans-serif"


def valid_color(value):
    """The colour itself when it is a #RRGGBB code, else None."""
    return value if isinstance(value, str) and _HEX.fullmatch(value) else None


def mix(color, ratio, other="#FFFFFF"):
    """``color`` moved towards ``other`` by ``ratio`` (0 = itself, 1 = other)."""
    a = [int(color[i : i + 2], 16) for i in (1, 3, 5)]
    b = [int(other[i : i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(
        f"{round(x + (y - x) * ratio):02X}" for x, y in zip(a, b, strict=True)
    )


def font_kind(data):
    """The font's format for a data URI, or None when the bytes are no font.

    ``data`` is the base64 an Odoo binary field holds."""
    if not data:
        return None
    try:
        head = base64.b64decode(data[:16], validate=False)[:4]
    except (binascii.Error, ValueError):
        return None
    if head == b"wOFF":
        return "woff"
    if head == b"OTTO":
        return "opentype"
    return "truetype" if head in _FONT_MAGIC else None


class BfSurveyPdfTheme(models.Model):
    _name = "bf.survey.pdf.theme"
    _description = "Survey PDF theme"
    _order = "name, id"

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company",
        help="Leave empty to offer this theme to every company.",
    )
    kicker = fields.Char(
        string="Kicker",
        translate=True,
        help="A short line above the title, such as the name of a programme.",
    )
    signature = fields.Char(
        string="Footer signature",
        help="Printed at the foot of every page. Empty: the company's name.",
    )
    color_primary = fields.Char(
        string="Main colour",
        help="Header band, section titles and numbers. A #RRGGBB code.",
    )
    color_accent = fields.Char(
        string="Accent colour",
        help="The rule under the header and the marks beside titles. A #RRGGBB code.",
    )
    color_text = fields.Char(
        string="Text colour",
        help="A #RRGGBB code. Empty: a dark grey.",
    )
    logo = fields.Image(
        string="Logo on the band",
        max_width=1024,
        max_height=1024,
        help="Shown on the coloured band: a light or white version of the logo "
        "reads best. Empty: the company's logo, on a white tile.",
    )
    font_family = fields.Char(
        string="Font",
        help="A font installed on the server, such as Lexend or "
        "'Liberation Sans', Arial. Ignored when a font file is given below.",
    )
    font_regular = fields.Binary(
        string="Font file (regular)",
        attachment=True,
        help="A TrueType, OpenType or WOFF file, embedded in each PDF.",
    )
    font_regular_name = fields.Char()
    font_bold = fields.Binary(
        string="Font file (bold)",
        attachment=True,
        help="Optional. Without it, bold text is drawn from the regular file.",
    )
    font_bold_name = fields.Char()

    @api.constrains("color_primary", "color_accent", "color_text")
    def _check_colors(self):
        for theme in self:
            for name in ("color_primary", "color_accent", "color_text"):
                value = theme[name]
                if value and not valid_color(value):
                    raise ValidationError(
                        self.env._(
                            "%(field)s must be a colour code such as #1F3864.",
                            field=theme._fields[name].get_description(self.env)[
                                "string"
                            ],
                        )
                    )

    @api.constrains("font_family")
    def _check_font_family(self):
        for theme in self:
            if theme.font_family and not _FAMILY.fullmatch(theme.font_family):
                raise ValidationError(
                    self.env._(
                        "A font name holds letters, digits, spaces, commas, "
                        "hyphens and quotes only."
                    )
                )

    @api.constrains("font_regular", "font_bold")
    def _check_font_files(self):
        for theme in self:
            for name in ("font_regular", "font_bold"):
                data = theme.with_context(bin_size=False)[name]
                if data and len(data) * 3 // 4 > _FONT_MAX_BYTES:
                    raise ValidationError(
                        self.env._("A font file may weigh up to 1 MB.")
                    )
                if data and not font_kind(data):
                    raise ValidationError(
                        self.env._(
                            "This file is not a font: give a TrueType, "
                            "OpenType or WOFF file."
                        )
                    )

    # ------------------------------------------------------------------
    # What a PDF is drawn with
    # ------------------------------------------------------------------

    @api.model
    def _bf_resolve(self, theme):
        """The colours, logo, font and words a PDF is drawn with.

        ``theme`` may be empty: the company's colours and logo stand in, then
        neutral ones. Any value that is not in a safe shape is replaced by
        its default, whatever wrote it to the database.
        """
        # A theme is presentation only. The one an administrator picked on a
        # survey is drawn for whoever may print the survey, even when the
        # theme belongs to a company the printing person is not in.
        theme = theme[:1].sudo()
        company = self.env.company
        primary = (
            valid_color(theme.color_primary)
            or valid_color(company.primary_color)
            or DEFAULT_PRIMARY
        )
        accent = (
            valid_color(theme.color_accent)
            or valid_color(company.secondary_color)
            or DEFAULT_ACCENT
        )
        text = valid_color(theme.color_text) or DEFAULT_TEXT
        logo, boxed = theme.logo, False
        if not logo:
            logo, boxed = company.logo, True
        faces = []
        family = DEFAULT_FAMILY
        if theme.font_family and _FAMILY.fullmatch(theme.font_family):
            family = f"{theme.font_family}, sans-serif"
        for name, weight in (("font_regular", "normal"), ("font_bold", "bold")):
            data = theme[name]
            kind = font_kind(data)
            if not kind:
                continue
            uri = f"data:font/{kind};base64,{data.decode()}"
            faces.append(
                f"@font-face {{ font-family: 'BF Survey Theme'; "
                f"font-weight: {weight}; src: url({uri}) format('{kind}'); }}"
            )
        if faces:
            family = f"'BF Survey Theme', {DEFAULT_FAMILY}"
        return {
            "primary": primary,
            "accent": accent,
            "text": text,
            "muted": mix(text, 0.35),
            "faint": mix(text, 0.55),
            "pale": mix(primary, 0.92),
            "rule": mix(text, 0.85),
            "family": family,
            "faces": Markup("\n".join(faces)),
            "logo": logo,
            "logo_boxed": boxed,
            "kicker": theme.kicker or "",
            "signature": theme.signature or company.name or "",
        }

    @api.model
    def _bf_css(self, style):
        """The stylesheet of both PDFs, from a resolved style (_bf_resolve).

        Built here rather than in the template: inside <style>, QWeb's HTML
        escaping would turn the quotes of a font name into entities that CSS
        does not read. Every interpolated value was checked by _bf_resolve.
        """
        s = style
        css = f"""
{s["faces"]}
.bfx, .bfx-foot {{ font-family: {s["family"]}; color: {s["text"]}; }}
.bfx {{ font-size: 10pt; line-height: 1.35; }}
.bfx p {{ margin: 0 0 5px 0; }}
/* Bootstrap gives row groups a solid border style; wkhtmltopdf then draws
   a frame around every table. Reset them all, then draw our own rules. */
.bfx table, .bfx thead, .bfx tbody, .bfx tfoot, .bfx tr, .bfx th, .bfx td {{ border: 0 none; }}
.bfx .band {{ width: 100%; border-collapse: collapse; background: {s["primary"]}; }}
.bfx .band td {{ padding: 14px 18px; vertical-align: middle; color: #fff; }}
.bfx .band td.logo {{ width: 1%; padding-right: 4px; }}
.bfx .band img {{ max-height: 64px; max-width: 230px; }}
.bfx .band .tile {{ background: #fff; padding: 6px 8px; border-radius: 3px; }}
.bfx .band .tile img {{ max-height: 48px; max-width: 200px; }}
.bfx .kicker {{ font-size: 9pt; font-weight: bold; letter-spacing: 1.5px;
  text-transform: uppercase; color: {mix(s["accent"], 0.45)}; }}
.bfx .title {{ font-size: 18pt; font-weight: bold; line-height: 1.15; margin-top: 2px; }}
.bfx .subtitle {{ font-size: 9.5pt; margin-top: 4px; color: {mix(s["primary"], 0.8)}; }}
.bfx .rule {{ height: 5px; background: {s["accent"]}; margin-bottom: 12px; }}
.bfx .meta {{ width: 100%; border-collapse: collapse; background: {s["pale"]}; margin-bottom: 12px; }}
.bfx .meta td {{ padding: 7px 18px; vertical-align: top; }}
.bfx .meta .label {{ font-size: 7.5pt; text-transform: uppercase; font-weight: bold;
  letter-spacing: 0.4px; color: {s["muted"]}; }}
.bfx .meta .value {{ font-size: 10.5pt; font-weight: bold; color: {s["primary"]}; }}
.bfx .intro {{ margin: 0 0 8px 0; }}
.bfx h2 {{ font-size: 13pt; color: {s["primary"]}; border-bottom: 2px solid {s["accent"]};
  padding-bottom: 3px; margin: 14px 0 6px 0; page-break-after: avoid; }}
.bfx .section-help {{ color: {s["muted"]}; font-size: 9pt; margin: -2px 0 6px 0; }}
.bfx .muted {{ color: {s["muted"]}; }}
.bfx .none {{ color: {s["faint"]}; font-weight: normal; font-style: italic; }}
.bfx .block {{ page-break-inside: avoid; }}
.bfx .q {{ border-bottom: 1px solid {s["rule"]}; padding: 6px 0 8px 0; }}
.bfx .q table.head {{ width: 100%; border-collapse: collapse; }}
.bfx .q table.head td {{ padding: 0; }}
.bfx .q td.num {{ width: 38px; vertical-align: top; color: {s["primary"]}; font-weight: bold; }}
.bfx .q td.body {{ vertical-align: top; }}
.bfx .q .qt {{ font-weight: bold; }}
.bfx .q .help {{ color: {s["muted"]}; font-size: 8.8pt; white-space: pre-wrap; margin-top: 1px; }}
.bfx .tag {{ display: inline-block; font-size: 7.3pt; padding: 0 6px; margin: 3px 4px 0 0;
  border-radius: 7px; background: {s["pale"]}; color: {s["primary"]}; }}
.bfx .tag.req {{ background: {s["accent"]}; color: #fff; font-weight: bold; }}
.bfx .tag.cond {{ background: #fff; border: 1px solid {s["accent"]}; color: {s["text"]}; }}
.bfx .answers {{ margin: 5px 0 0 0; }}
.bfx .opt {{ margin: 0 0 3px 0; }}
.bfx .box {{ display: inline-block; width: 9px; height: 9px; border: 1.3px solid {s["primary"]};
  vertical-align: -1px; margin: 0 6px 0 1px; }}
.bfx .box.round {{ border-radius: 6px; }}
.bfx .field {{ border-bottom: 1.3px solid {s["primary"]}; background: {s["pale"]}; height: 20px; }}
.bfx .field.tall {{ height: 46px; }}
.bfx .specify {{ margin-top: 5px; font-size: 8.8pt; color: {s["muted"]}; font-style: italic; }}
.bfx table.bfx-grid {{ width: 100%; border-collapse: collapse; font-size: 9pt; margin-top: 4px; }}
.bfx table.bfx-grid th {{ background: {s["primary"]}; color: #fff; padding: 3px 6px; font-size: 8.3pt; }}
.bfx table.bfx-grid td {{ border-bottom: 1px solid {s["rule"]}; padding: 3px 6px; text-align: center; }}
.bfx table.bfx-grid td.bfx-rl {{ text-align: left; }}
.bfx table.bfx-grid td.pct {{ text-align: right; width: 90px; }}
.bfx table.bfx-grid tr.total td {{ font-weight: bold; border-top: 1.5px solid {s["primary"]}; }}
.bfx .cell {{ display: inline-block; width: 40px; height: 13px; border-bottom: 1.2px solid {s["primary"]};
  background: {s["pale"]}; vertical-align: middle; }}
.bfx .scale span {{ display: inline-block; min-width: 18px; text-align: center; margin-right: 4px;
  border: 1.2px solid {s["primary"]}; border-radius: 10px; font-size: 8.5pt; padding: 0 3px; }}
.bfx table.qa {{ width: 100%; border-collapse: collapse; }}
.bfx table.qa tr {{ page-break-inside: avoid; }}
.bfx table.qa td {{ border-bottom: 1px solid {s["rule"]}; padding: 5px 6px; vertical-align: top; }}
.bfx table.qa td.ql {{ width: 42%; color: {s["muted"]}; font-size: 9pt; }}
.bfx table.qa td.qr {{ font-weight: bold; }}
.bfx table.qa ul {{ margin: 0; padding-left: 14px; }}
.bfx table.qa .long {{ white-space: pre-wrap; font-weight: normal; }}
.bfx table.qa .comment {{ font-weight: normal; margin-top: 3px; }}
.bfx table.mx {{ width: 100%; border-collapse: collapse; }}
.bfx table.mx td {{ border: none; border-bottom: 1px dotted {s["rule"]}; padding: 2px 0; font-size: 9pt; }}
.bfx table.mx td.mr {{ font-weight: normal; padding-right: 10px; }}
.bfx table.mx td.mv {{ width: 30%; text-align: right; color: {s["primary"]}; }}
.bfx table.mx td.bar {{ width: 45%; }}
.bfx .rail {{ background: {s["pale"]}; height: 9px; }}
.bfx .fill {{ background: {s["accent"]}; height: 9px; }}
.bfx .end {{ margin-top: 18px; padding-top: 8px; border-top: 2px solid {s["accent"]}; }}
.bfx-foot {{ font-size: 7.5pt; color: {s["muted"]}; border-top: 1px solid {s["accent"]}; padding-top: 4px; }}
.bfx-foot .pages {{ float: right; }}
"""
        return Markup(css)
