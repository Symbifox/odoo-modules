# -*- coding: utf-8 -*-
"""Socle des essais : aucun accès réseau, tout passe par des flux d'exemple.

Les exemples reprennent des cas typiques de flux de communiqués :
un communiqué bilingue sous un même identifiant, un communiqué reçu par deux
flux, Bombardier civil et militaire, une convocation d'appel de résultats et
le faux positif « Combat Sports ».
"""
import os
from contextlib import contextmanager
from unittest.mock import patch

from odoo.tests.common import TransactionCase

from odoo.addons.bf_flux.models.flux_source import FluxErreur, FluxSource

ICI = os.path.join(os.path.dirname(__file__), "fixtures")


def fixture(nom):
    with open(os.path.join(ICI, nom), "rb") as fh:
        return fh.read()


class FluxCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Users = cls.env["res.users"].with_context(no_reset_password=True)
        cls.g_interne = cls.env.ref("base.group_user")
        cls.g_gestion = cls.env.ref("bf_flux.group_flux_gestion")
        cls.u_gestion = Users.create({
            "name": "Gestion des flux", "login": "flux_gestion",
            "email": "gestion@exemple.test",
            "groups_id": [(6, 0, [cls.g_gestion.id])]})
        cls.u_atelier = Users.create({
            "name": "Personne de l'atelier", "login": "flux_atelier",
            "email": "atelier@exemple.test",
            "groups_id": [(6, 0, [cls.g_interne.id])]})
        cls.u_projet = Users.create({
            "name": "Personne du projet", "login": "flux_projet",
            "email": "projet@exemple.test",
            "groups_id": [(6, 0, [cls.g_interne.id])]})
        cls.u_ailleurs = Users.create({
            "name": "Personne d'ailleurs", "login": "flux_ailleurs",
            "email": "ailleurs@exemple.test",
            "groups_id": [(6, 0, [cls.g_interne.id])]})
        cls.dept = cls.env["hr.department"].create({"name": "Veille"})
        cls.sous_dept = cls.env["hr.department"].create(
            {"name": "Veille terrain", "parent_id": cls.dept.id})
        cls.env["hr.employee"].create({
            "name": "Atelier", "user_id": cls.u_atelier.id,
            "department_id": cls.sous_dept.id})
        cls.env["hr.employee"].create({
            "name": "Ailleurs", "user_id": cls.u_ailleurs.id})
        cls.projet = cls.env["project.project"].create({"name": "Projet d'essai"})
        cls.projet.message_subscribe(partner_ids=cls.u_projet.partner_id.ids)

        Source = cls.env["bf.flux.source"].with_user(cls.u_gestion)
        cls.src_defense = Source.create({
            "name": "Défense", "url": "https://flux.example.com/defense",
            "langue_preferee": "fr"})
        cls.src_aero = Source.create({
            "name": "Aérospatiale", "url": "https://flux.example.com/aero",
            "langue_preferee": "fr"})

    @contextmanager
    def reseau(self, reponses):
        """Remplace le réseau : `reponses` associe une adresse à des octets,
        ou à une FluxErreur à lever."""
        appels = []

        def faux(source, url, **kw):
            appels.append(url)
            rep = reponses.get(url)
            if rep is None:
                raise FluxErreur("HTTP 404", passager=False)
            if isinstance(rep, Exception):
                raise rep
            if isinstance(rep, tuple):
                return rep
            return rep, "application/rss+xml"

        with patch.object(FluxSource, "_flux_telecharger", faux):
            yield appels

    def flux_exemple(self):
        return {
            "https://flux.example.com/defense": fixture("defense.xml"),
            "https://flux.example.com/aero": fixture("aero.xml"),
        }
