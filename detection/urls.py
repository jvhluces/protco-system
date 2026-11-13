from django.urls import path
from . import views

app_name = "detection"

urlpatterns = [

    path("", views.upload_and_detect, name="dashboard"),
    path("damage_check/", views.upload_and_detect, name="damage_check"),
    path("weather/", views.weather_forecast, name="weather_forecast"),
    path("reports/", views.reports_list, name="reports"),
    path("report/<int:report_id>/", views.report_detail, name="report_detail"),
    path("report/<int:report_id>/delete/", views.delete_report, name="delete_report"),
    path('settings/', views.settings_view, name='settings'),
    path('logout/', views.logout_view, name='logout'),
    path("report/<int:report_id>/submit/", views.submit_report, name="submit_report"),
    path("dashboard/", views.dashboard, name="dashboard"),
]