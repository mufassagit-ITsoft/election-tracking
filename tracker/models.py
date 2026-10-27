from django.db import models

__author__ = "Mustafa Prodhan"
class Election(models.Model):
    # Focus on the data model for any year, not just midterms.
    year = models.PositiveBigIntegerField()
    kind = models.CharField(max_length=20, default="midterm") # midterm | presidential | special
    date = models.DateField()
    name = models.CharField(max_length=120)

    class Meta:
        unique_together = [("year", "kind", "date")]
        ordering = ["-date"]

    def __str__(self):
        return self.name

class Race(models.Model):
    class Office(models.TextChoices):
        SENATE = "senate"
        HOUSE = "house"
        GOVERNOR = "governor"
        AG = "attorney_general"
        OTHER = "other"

    election = models.ForeignKey(Election, on_delete=models.CASCADE, related_name="races")
    office = models.CharField(max_length=20, choices=Office.choices)
    state = models.CharField(max_length=2)
    district = models.CharField(max_length=10, blank=True) # House only, possibly state legislature
    is_special = models.BooleanField(default=False)
    civic_id = models.CharField(max_length=80, blank=True, db_index=True) # CivicAPI's race id
    incumbent = models.CharField(max_length=120, blank=True)
    incumbent_party = models.CharField(max_length=10, blank=True)
    seat_status = models.CharField(max_length=200, blank=True) # open | running | incumbent
    status_text = models.CharField(max_length=200, blank=True)
    candidates_text = models.TextField(blank=True) # human summary until live candidates arrive
    note = models.TextField(blank=True)
    civic_updated = models.DateTimeField(null=True, blank=True) # How CivicAPI updates after a previous one.

    class Meta:
        unique_together = [("election", "office", "state", "district", "is_special")]
        indexes = [models.Index(fields=["election", "office", "state"])]

    @property
    def label(self):
        return f"{self.state}-{self.district}" if self.district else self.state

    def __str__(self):
        return f"{self.election.year} {self.office} {self.label}"

class Candidate(models.Model):
    race = models.ForeignKey(Race, on_delete=models.CASCADE, related_name="candidates")
    name = models.CharField(max_length=120)
    party = models.CharField(max_length=40, blank=True)
    is_incumbent = models.BooleanField(default=False)
    civic_id = models.BigIntegerField(null=True, blank=True) # CivicAPI candidate id
    color = models.CharField(max_length=9, blank=True)

    class Meta:
        unique_together = [("race", "name")]

class Rating(models.Model):
    # Forecast rating from a rater (cook / sabato / inside).
    race = models.ForeignKey(Race, on_delete=models.CASCADE, related_name="ratings")
    rater = models.CharField(max_length=20)
    rating = models.CharField(max_length=30)  # e.g. "Lean D", "Tossup", "Solid Republican"
    as_of = models.CharField(max_length=40, blank=True)

    class Meta:
        unique_together = [("race", "rater")]

class ResultSnapshot(models.Model):
    # This is how race calls come in
    race = models.ForeignKey(Race, on_delete=models.CASCADE, related_name="snapshots")
    fetched_at = models.DateTimeField(auto_now_add=True, db_index=True)
    percent_reporting = models.FloatField(null=True, blank=True)
    total_votes = models.BigIntegerField(null=True, blank=True)
    called = models.BooleanField(default=False)
    winner_name = models.CharField(max_length=120, blank=True)
    winner_party = models.CharField(max_length=40, blank=True)
    raw = models.JSONField(default=dict)

    class Meta:
        ordering = ["-fetched_at"]
        get_latest_by = "fetched_at"

class CandidateResult(models.Model):
    snapshot = models.ForeignKey(ResultSnapshot, on_delete=models.CASCADE, related_name="results")
    name = models.CharField(max_length=120)
    party = models.CharField(max_length=40, blank=True)
    votes = models.BigIntegerField(null=True, blank=True)
    percent = models.FloatField(null=True, blank=True)
    is_winner = models.BooleanField(default=False)

class EarlyVoteSnapshot(models.Model):
    # State by state result
    election = models.ForeignKey(Election, on_delete=models.CASCADE, related_name="early_votes")
    state = models.CharField(max_length=2)
    as_of = models.DateField(null=True, blank=True)
    fetched_at = models.DateTimeField(auto_now_add=True)
    total = models.BigIntegerField(null=True, blank=True)
    mail_requested = models.BigIntegerField(null=True, blank=True)
    mail_returned = models.BigIntegerField(null=True, blank=True)
    in_person = models.BigIntegerField(null=True, blank=True)
    dem = models.BigIntegerField(null=True, blank=True)
    rep = models.BigIntegerField(null=True, blank=True)
    other = models.BigIntegerField(null=True, blank=True)
    no_affiliation = models.BigIntegerField(null=True, blank=True)
    prior_final = models.BigIntegerField(null=True, blank=True)  # same election type, prior cycle
    source = models.CharField(max_length=80, blank=True)
    flags = models.TextField(blank=True)
 
    class Meta:
        indexes = [models.Index(fields=["election", "state", "-as_of"])]
        ordering = ["state", "-as_of", "-fetched_at"]
 
    @property
    def pct_of_prior(self):
        if self.total and self.prior_final:
            return round(100 * self.total / self.prior_final, 1)
        return None
 
 
class SyncLog(models.Model):
    started_at = models.DateTimeField(auto_now_add=True)
    kind = models.CharField(max_length=30)
    ok = models.BooleanField(default=True)
    detail = models.TextField(blank=True)
 
    class Meta:
        ordering = ["-started_at"]

class EarlyVoteRegion(models.Model):
    # Early vote JSON data
    election = models.ForeignKey(Election, on_delete=models.CASCADE, related_name="early_regions")
    state = models.CharField(max_length=2)
    region = models.CharField(max_length=80)
    category = models.CharField(max_length=12)  # requested | returned | inperson | grace
    party = models.CharField(max_length=30)     # Democratic | Republican | Other | Unspecified
    votes = models.BigIntegerField(default=0)
    snapshot_date = models.DateField()
 
    class Meta:
        unique_together = [("election", "state", "region", "category", "party", "snapshot_date")]
        indexes = [models.Index(fields=["election", "state", "category", "snapshot_date"])]
 
 
class Forecast(models.Model):
    # civicAPI's own forecast per state (senate/governor) or district (house).
    election = models.ForeignKey(Election, on_delete=models.CASCADE, related_name="forecasts")
    office = models.CharField(max_length=20)
    key = models.CharField(max_length=10)  # AK, or AZ-6 for House
    rater = models.CharField(max_length=20, default="civicapi")
    rating = models.CharField(max_length=30, blank=True)
    dem_pct = models.FloatField(null=True, blank=True)
    rep_pct = models.FloatField(null=True, blank=True)
    ind_pct = models.FloatField(null=True, blank=True)
    dem_name = models.CharField(max_length=120, blank=True)
    rep_name = models.CharField(max_length=120, blank=True)
    ind_name = models.CharField(max_length=120, blank=True)
    incumbent_party = models.CharField(max_length=10, blank=True)
    as_of = models.CharField(max_length=20, blank=True)
    raw = models.JSONField(default=dict)
 
    class Meta:
        unique_together = [("election", "office", "key", "rater")]
 
 
class MapShape(models.Model):
    # Map geometry stored in SQLite so the GUI needs no files. 
    layer = models.CharField(max_length=20)
    key = models.CharField(max_length=10) # AZ, or AZ-6
    name = models.CharField(max_length=60, blank=True)
    geometry = models.JSONField() # GeoJSON geometry (states) or {"x":..,"y":..,"w":..} (tiles)
 
    class Meta:
        unique_together = [("layer", "key")]