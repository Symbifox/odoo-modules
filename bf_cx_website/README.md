# bf_cx_website: public testimonials

Auto-installs when both `bf_cx` and `website` are installed. Publishes the
public `/temoignages` page, which dynamically renders testimonials in the
"Published" state (quote, client name, client company), filtered on the
current website's company. Nothing is sent to clients and no site menu is
added: the site owner decides where to link the `/temoignages` URL.

Law 25 compliance: a testimonial that is pulled (any state other than
"Published") disappears from the site instantly, since rendering is
dynamic.
