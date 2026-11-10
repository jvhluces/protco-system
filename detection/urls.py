from django.urls import path
from . import views

app_name = "detection"

urlpatterns = [
    # Dashboard -> upload.html  |  Damage Check -> damage_check.html
    # (iisang view; ang url_name ang nagpapasya kung anong template)
    path("", views.upload_and_detect, name="dashboard"),
    path("damage_check/", views.upload_and_detect, name="damage_check"),
    path("weather/", views.weather_forecast, name="weather_forecast"),
    path("reports/", views.reports_list, name="reports"),
    path("report/<int:report_id>/", views.report_detail, name="report_detail"),
    path("report/<int:report_id>/delete/", views.delete_report, name="delete_report"),
]