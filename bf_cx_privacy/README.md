# bf_cx_privacy - Customer Experience to Privacy bridge (Law 25)

Auto-installs when both `bf_cx` and `privacy_consent` are installed.

- A "Publication of a client testimonial" purpose plus a versioned notice (hash).
- A "Privacy module" consent mode (`selection_add`): a formal request by
  email, publication locked until consent is GRANTED, and
  withdrawal/expiry automatically pulling the published testimonial.
- The "Do not contact" list blocks every bf_cx solicitation.

## Languages (18.0.1.1.1)

The module is written in English and ships a `fr_CA` catalogue: a French-speaking user reads it
in French, an English-speaking one (`en_US`, `en_CA`) in English. Before this version the source
was French, so English-speaking users read it in French.

- The purpose and the notice names switch to the English source on upgrade only while they still
  carry the text the module shipped; a renamed one is left as it is. The notice body and its
  version are unchanged, so existing consents keep their proof.
- The chatter trace left when a consent is refused, withdrawn or expired is written in the
  company's language (not that of the client's public page or of the expiry job) and names the
  status by its label rather than its technical key.
