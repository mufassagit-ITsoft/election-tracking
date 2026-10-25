from django.urls import path
from . import views
 
urlpatterns = [
    path("health/", views.health),
    path("elections/", views.elections),
    path("<int:year>/summary/", views.summary),
    path("<int:year>/races/", views.races),
    path("<int:year>/map/<str:office>/", views.map_data),
    path("<int:year>/results/live/", views.live_results),
    path("<int:year>/early-vote/", views.early_vote),
]