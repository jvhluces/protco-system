from django.urls import path
from . import views

app_name = "detection"

urlpatterns = [
    path('', views.upload_and_detect, name='upload_and_detect'),
    path('damage_check/', views.upload_and_detect, name='damage_check'),  # ✅ IDINAGDAG ITO
]