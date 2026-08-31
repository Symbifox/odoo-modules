# Link Pages (`bf_linkpage`)

A public page gathering one person's links under a short URL, plus the QR code
to drop into an email signature.

## What this adds over a hosted link-page service

The links already exist in the database. The booking link, the secure upload
page, the phone number and the email address are records, not strings retyped by
hand. The page **resolves them at render time**.

The intended consequence: when someone's booking slug changes, their page
follows with nobody touching it, and the QR code already printed in their
signature keeps pointing to the right place. That is the one thing an outside
service cannot do.

## The three decisions that govern the module

### 1. An unknown slug returns a plain 404

The sibling module `bf_appointment` silently redirects to its index when a slug
does not resolve. That is acceptable where the address is clicked from an email
somebody can correct and resend. Here the address goes into a **printed QR
code**: it cannot be corrected. A redirect would produce a page that displays,
so the appearance of success, and nobody would know the QR lands elsewhere.

Corollary: every refusal returns the same 404. A missing slug, a draft page, a
closed, archived or expired one are indistinguishable from outside, otherwise
the address becomes an oracle confirming to an anonymous visitor which slugs
exist.

### 2. A source that fails to resolve makes its link disappear

It does not return an approximate address and it sends nobody to a home page. A
dead link reached by an already-printed QR code costs more than an absent one.
The gap between "links" and "visible links" in the back office is the only way
to see that a source has gone silent.

### 3. A one-off page carries an expiry, armed at creation

A public page with no owner that nobody revokes is the same blind spot as an
eternal share. The expiry is a **date read at render time**, not a state to
maintain: no scheduled job needs to run for a page to close. The default is 90
days, adjustable through the `bf_linkpage.oneoff_expiry_days` system parameter.

## The URL prefix

Pages are served under `/l/<slug>`, never at the root. A slug served at
`/<slug>` collides with website routing (website pages, `/shop`, `/blog`), and
the language prefix is stripped before routing, which makes the collision
intermittent and therefore hard to see. The dedicated prefix costs two
characters in the QR code and removes the entire class of failures.

## The sources

| Code | Resolves to | Provider required |
| --- | --- | --- |
| `manual` | The address typed into the link | none |
| `appointment` | The person's booking page, through their resource. `booking_slug` on the page wins | `bf_appointment` |
| `securetransfer` | The `/to/<slug>` upload page, matched by the brand's **owner** then by slug | `bf_securetransfer` |
| `meet` | The permanent room recorded on the page (`meet_url`) | none |
| `partner_email` | `mailto:` from the contact record | none |
| `phone_work` | The employee record's work phone, the company's otherwise | none |
| `phone_mobile` | The contact's mobile, the employee record's otherwise | none |
| `phone_direct` | The contact record's "phone" field | none |
| `phone_tollfree` | The company's toll-free number | none |
| `partner_phone` | *Legacy*: the contact's mobile then phone, without saying which | none |
| `partner_website` | The contact's website | none |
| `social_linkedin` | The contact's `x_linkedin_url`, the company page otherwise | none |
| `social_github`, `social_instagram`, `social_facebook`, `social_youtube`, `social_twitter` | The company's `social_*` fields | none |

> **The toll-free number lives in the company record's "mobile" field**, for want
> of a dedicated one in Odoo. That is not a data-entry mistake.

> **`partner_phone` does not say which number it serves.** It is kept for
> existing pages; the four `phone_*` sources name theirs. A business signature's
> number is almost always `phone_work`, which lives on the EMPLOYEE record and
> not on the contact.

The `social_*` sources render as a **row of icons**, with no label and the
network name in `aria-label`. Their address is **read**, never typed: that is
what lets a template lay them down, whereas an address typed onto a template
link would be wiped at the next refresh.

**No import of a provider module.** The module depends on neither
`bf_appointment` nor `bf_securetransfer`: it checks the registry for the model
and stays quiet otherwise. An import would break installation wherever the
provider is absent.

A satellite module adds a source by overriding `_sources()` on the abstract
`bf.linkpage.source` model and defining the matching `_resolve_<code>` method.

## The QR code

`GET /l/<slug>/qr.png` — restricted to signed-in members of the module. The code
only ever encodes a public address, but composing an image on demand from a
public route is a convenient lever for exhausting a server; it is downloaded
once, by the person assembling their signature.

- `?branded=0` — without the logo.
- `&size=6` — a more compact rendering (4 to 20).

The branded code is produced at **error-correction level H** because the logo
masks modules of the code, and the logo is capped at 22% of the side. Lowering
that level yields a code that reads on screen and then fails once printed small
in a signature.

### The settings, on the record

A **QR code** tab carries the logo (the page's own wins over the company's), the
plate behind it, the size, both colours, a preview that is the real rendering,
and a download button.

### The contrast guard, and what it measured

Two mechanical rules rather than a matter of taste:

1. the contrast ratio must reach **4:1**;
2. the code must be **darker** than its background.

The second is not a precaution held on principle. Measured with a decoder, a
code lighter than its background reads at **no size at all**: not blue on
charcoal, not even white on charcoal. A setting failing either rule falls back
to black on white **and says so** in a warning on the record.

> **A brand colour does not necessarily pass.** `#29ABE1` on white reaches only
> **2.6:1**. It is the colour anyone reaches for first, and it would produce
> handsome codes that nobody can scan, the defect surfacing only once printed.
> Those same two brand colours work the other way round, charcoal on blue, down
> to 90 px and not 70.

### The logo plate

Three values: the code's background, the code's own colour, or none.

"The code's colour" exists for a measured reason: **a logo sharing the code's
background colour vanishes on it**. On a charcoal code over a blue background, a
blue logo becomes invisible while the code itself still decodes perfectly. The
defect is in the logo, not the code, and no decode check catches it.

> **The logo must be a raster image.** An SVG is a perfectly valid image, it
> renders everywhere else in Odoo, and the imaging library cannot embed it. The
> module says so on the record instead of silently producing an unbranded code.

## Templates, layouts and theme

A **template** carries a set of links AND a look: layout, theme, accent colour.
Seven are seeded, and five layouts exist (cards, raised cards, minimal, filled
buttons, technical).

**The look is applied only on FIRST attachment**, or on an explicit "apply
template" click. The periodic pass never reapplies it: otherwise a colour
somebody chose would be undone overnight.

The theme follows the device by default, with a toggle offered to the visitor.
Their choice lives in THEIR browser, in `localStorage`, and is never sent to the
server: it is a display preference, not a tracker.

> **The toggle is an asset file, not an inline script.** The site's policy is
> `script-src 'self'` WITHOUT `unsafe-inline`: a `<script>` in the template
> would be blocked by the browser, silently.

## The automation, and what it deliberately does not do

A daily pass creates the missing page of every active employee and refreshes the
ones following a template. Settings live under Settings → Link Pages.

A **periodic pass** rather than an override of `hr.employee.create`, for three
reasons in order: the module depends on no provider and depending on `hr` would
cost it that property; a pass catches employees created before it was enabled,
arriving through an import, or completed afterwards; creation and refresh become
the same gesture, reading the gap between what should be and what is.

The accepted price: a new employee's page appears at the next run.

**A link added by hand survives the refresh.** Only `from_template` links are
replaced. That property, and only that, is what makes a periodic pass
acceptable. Values belonging to the person (`booking_slug`, `meet_url`) live on
the PAGE and never on a link, for the same reason.

## The contact card

`GET /l/<slug>/vcard.vcf`, and an "add to my contacts" button offered by
default. In **vCard 3.0** rather than 4.0: 4.0 is cleaner and less widely
accepted, and a card a phone refuses to open is worth nothing.

It carries the SAME numbers as the page, in the same order, and de-duplicates a
number entered in two places. The page's address travels with it as the URL:
saved contact details go stale, the link to the page stays right.

A one-off page carries no person and therefore offers no card.

> vCard separators (`,` `;` `\`) are escaped. An organisation name containing a
> comma would split the card into two fields on import, with no error anywhere.

## Languages

The page is served at `/l/<slug>` and `/<lang>/l/<slug>`, with a selector made
of **real links to real addresses**: the English version has to be shareable,
bookmarkable and indexable under its own.

Labels, subtitles, headline and bio are translatable. A template line's
translations are **carried across language by language** onto the link it
creates: without that, the nightly pass running in `fr_CA` would recreate
French-only links and the English page would fall back to French with nothing
signalling it.

> **Module data text lives in the `en_US` slot even when it is French.** Writing
> English into it without first WRITING the French into `fr_CA` takes the French
> with it.

> **Odoo extracts a view's translatable terms including the neighbouring inline
> markup.** Text wrapped by an icon inside a `span` yields a single term,
> indentation included, and the translation breaks the first time the template is
> reformatted. Put the text in a block.

## What the page counts

Visits per page, clicks per link, shown in the **Statistics** tab. A marked gap
between the two says people arrive and leave without opening anything. It is the
only way to know whether a signature's QR code is actually used.

Every link carries a **shown** flag: unticking it removes the link from the page
without deleting it.

> **`active_test: False` is mandatory on the links one2many** in the view.
> Without it, unticking "shown" makes the row DISAPPEAR from the list and nothing
> can bring it back. A button that hides without allowing un-hiding is worse than
> no button.

## Two traps visible ONLY through a browser

**The `bin_size` context.** The web client reads binary fields asking for their
HUMAN-READABLE SIZE, not their content: the field returns `b"32.99 Kb"`, which
any image processing refuses. In `odoo shell` the context is not set and
everything works, so **a shell check proves nothing** for a binary field. Force
`bin_size=False` at the point of reading.

**A served image's type must be DERIVED from its bytes.** The site sets
`X-Content-Type-Options: nosniff`: announcing `image/png` for an SVG gives a
route answering 200 with the right byte count and a broken image on screen.
Nothing in the logs.

## Deliberately out of scope

**The custom domain.** Every domain needs a proxy host and a certificate placed
by hand, plus the `website.domain` configuration on the Odoo side. That is
recurring hosting chore work adding nothing to the module. A client explicitly
asking for one is handled as hosting work, not as a feature.

## What the QA established

75 tests, green on a fresh install with AND without the provider modules. Every
invariant was submitted to a mutation: break the rule in the code and require
the suite to go red. 18 mutations out of 19 do.

**The only one that does not**, and it is accepted: putting the visit counter
back to read-modify-write instead of the database-side increment. Losing visits
under load is not demonstrable inside a single transaction. The SQL increment
remains a precaution no test covers.

Four defects found on that occasion, all fixed:

- **The photo did not display.** Served through
  `/web/image/bf.linkpage/<id>/avatar`, it reached the visitor as a placeholder
  image — 6078 bytes of generic silhouette where the photo itself is 77 —
  because the public user has no read access on the model and `/web/image`
  answers **200** rather than an error. The photo now goes through
  `/l/<slug>/avatar`.
- **A duplicate slug surfaced a raw database error.** The SQL constraint applies
  at INSERT, therefore before any Python constraint: the check moved into
  `create()` and `write()`.
- **A malformed `oneoff_expiry_days` prevented creating a page.** It falls back
  to 90 days.
- **`_compute_linkpage_count` had no `@api.depends`**, so it was never
  invalidated.

And one finding that must be read with its correction: the QA first believed it
had found an **open redirect** through the "contact website" source. On
verification, `res.partner.website` normalises on write —
`//example.invalid/x` becomes `http://example.invalid/x` — so none of the
sources can emit an executable scheme. The `_safe_url` filter was added anyway:
it covers the RESOLVED address, where the write constraint only saw the `url`
field, and a source added later will not lack the guard by accident.

## Running the tests

The bench has `list_db = False`. An **anonymous** request can then resolve a
database only if `dbfilter` designates exactly one. With `--db-filter='.*'`,
every public route returns a bare werkzeug 404, and the "must return 404"
assertions pass **without discriminating anything**. Always pin the filter to
the test database:

```sh
docker exec odoo-staging odoo -d dryrun_linkpage -i bf_linkpage \
    --test-enable --test-tags /bf_linkpage --stop-after-init \
    --http-port=8199 --db-filter='^dryrun_linkpage$'
```

`test_page_publiee_repond_200` is what makes the refusal tests meaningful: it
proves the data is visible to the server.
