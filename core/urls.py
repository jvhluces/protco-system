from django.contrib.auth import views as auth_views
from django.urls import path
from . import views

urlpatterns = [
    path('', views.admin_dashboard, name='admin_dashboard'),
    path('dashboard/', views.admin_dashboard, name='admin_dashboard'),
    path('login/', views.login_register_view, name='login'), # Isang file lang para sa lahat
    path('reports/', views.reports, name='reports'),
    path('users/', views.users, name='users'),
    path('windmap/', views.windmap, name='windmap'),
    path('schoolmap/', views.schoolmap, name='schoolmap'),
    path('settings/', views.settings_view, name='settings'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
]