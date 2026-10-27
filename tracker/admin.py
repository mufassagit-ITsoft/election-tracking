from django.contrib import admin
from . import models

__author__ = "Mustafa Prodhan"
for m in (models.Election, models.Race, models.Candidate, models.Rating,
          models.ResultSnapshot, models.CandidateResult, models.EarlyVoteSnapshot,
          models.EarlyVoteRegion, models.Forecast, models.MapShape, models.SyncLog):
    admin.site.register(m)
