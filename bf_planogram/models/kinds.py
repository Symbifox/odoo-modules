# -*- coding: utf-8 -*-
"""What a fixture can be, and how big one usually is.

One table, read by the model (the selection and the default dimensions), by the drawing on
screen, by the PDF and by the legend. A fixture posed from the list is already to scale, so
nobody types 183 the first time. The size of a kind is a PROPOSAL: it fills a dimension that
is still blank, and never corrects one typed by hand.

The dimensions are centimetres, and they are the ordinary retail sizes: a gondola section
is 48 inches wide and 72 high, a reach-in cooler is wider and deeper, an impulse rack at the
till is narrow. They are defaults, not limits.
"""

# (code, label, width cm, height cm, depth cm)
KINDS = [
    ("gondola", "Gondola", 122.0, 183.0, 56.0),
    ("endcap", "End cap", 122.0, 183.0, 56.0),
    ("cooler", "Cooler", 150.0, 200.0, 80.0),
    ("freezer", "Freezer", 180.0, 90.0, 70.0),
    ("counter", "Counter", 200.0, 100.0, 70.0),
    ("checkout", "Checkout display", 60.0, 140.0, 25.0),
    ("wall", "Wall run", 300.0, 220.0, 45.0),
    ("other", "Other", 120.0, 180.0, 50.0),
]


def selection_kinds():
    return [(c, n) for c, n, _w, _h, _d in KINDS]


def size_of_kind(code):
    return dict((c, (w, h, d)) for c, _n, w, h, d in KINDS).get(code, (120.0, 180.0, 50.0))


def label_of_kind(code):
    return dict(selection_kinds()).get(code, code)
