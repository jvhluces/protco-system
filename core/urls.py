from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.shortcuts import redirect

def home(request):
    return redirect('/damage/')

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", home, name="home"),
    path("damage/", include("detection.urls", namespace="detection")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)