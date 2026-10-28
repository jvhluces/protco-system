from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.http import HttpResponse

def home(request):
    return HttpResponse(
        "<h1>School Safety System</h1>"
        "<p>Welcome to the Infrastructure Damage Detection Platform</p>"
        "<a href='/damage/'>→ Go to Damage Detector</a>"
    )

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", home, name="home"),
    path("damage/", include("detection.urls", namespace="detection")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)