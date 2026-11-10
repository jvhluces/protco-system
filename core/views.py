from django.shortcuts import render

def login_register_view(request):
    # Hayaan na ang JavaScript at LocalStorage ang humawak ng login/register flow
    return render(request, 'login.html')

def admin_dashboard(request):
    context = {
        'report_count': 0,
        'user_count': 0,
    }
    return render(request, 'dashboard.html', context)

def reports(request):
    context = {
        'reports': [],
    }
    return render(request, 'reports.html', context)

def users(request):
    context = {}
    return render(request, 'users.html', context)

def windmap(request):
    context = {}
    return render(request, 'windmap.html', context)

def schoolmap(request):
    context = {}
    return render(request, 'schoolmap.html', context)

def settings_view(request):
    context = {}
    return render(request, 'settings.html', context)