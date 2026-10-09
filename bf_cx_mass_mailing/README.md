# bf_cx_mass_mailing: excluding open loops

Auto-installs when both `bf_cx` and `mass_mailing` are installed. Adds an
"Exclude open CX loops" checkbox to mass mailings (unticked by default):
on send, recipients whose contact has unhandled feedback awaiting a
callback, or an open complaint, are dropped from the list (matched on the
normalised email, covering both mailing lists and contacts). Nothing new
is sent; without the option, standard behaviour is unchanged.

## Languages (v18.0.1.1.0)

Labels and messages are written in English in the source; the French ships in `i18n/fr_CA.po`. Before this version they read in French for every user, including users set to English.
