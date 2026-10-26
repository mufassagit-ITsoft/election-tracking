from django.contrib import admin
from . import models

__author__ = "Mustafa Prodhan"
for m in (models.Election, models.Race, models.Candidate, models.Rating,
          models.ResultSnapshot, models.EarlyVoteSnapshot, models.SyncLog):
    admin.site.register(m)
