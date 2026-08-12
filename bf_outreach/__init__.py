# -*- coding: utf-8 -*-
from . import models
from . import wizard

from .models.post_init import register_universal_search


def post_init_hook(env):
    register_universal_search(env)
