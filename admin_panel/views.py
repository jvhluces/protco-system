from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from detection.models import DetectionResult

def login_register_view(request):
    if request.user.is_authenticated:
        return redirect('/admin-panel/dashboard/')

    if request.method == 'POST':
        action = request.POST.get('action', 'login')

        if action == 'register':
            # Kunin mo kahit ano yung name na ginamit mo sa HTML
            # para hindi sya mag-crash kung iba yung name mo
            username = request.POST.get('username') or request.POST.get('reg-contact') or request.POST.get('contact') or ''
            password = request.POST.get('password') or request.POST.get('reg-password') or ''
            first_name = request.POST.get('first_name') or request.POST.get('reg-first') or ''
            last_name = request.POST.get('last_name') or request.POST.get('reg-last') or ''

            username = username.strip()

            # ITO YUNG PUMIPIGIL SA ValueError MO
            if not username or not password:
                return render(request, 'login.html', {'error': 'Ilagay mo muna yung Contact Number at Password. Walang natanggap si Django.'})

            if User.objects.filter(username=username).exists():
                return render(request, 'login.html', {'error': 'May account na yung number na yan, mag-login ka na lang.'})

            User.objects.create_user(
                username=username,
                password=password,
                first_name=first_name,
                last_name=last_name,
            )
            return render(request, 'login.html', {'success': 'Account created! Mag-login ka na.'})

        else:
            username = request.POST.get('username') or ''
            password = request.POST.get('password') or ''
            user = authenticate(request, username=username, password=password)
            if user is not None:
                login(request, user)
                return redirect('/admin-panel/dashboard/')
            else:
                return render(request, 'login.html', {'error': 'No account found or incorrect password.'})

    return render(request, 'login.html')

@login_required
def admin_dashboard(request):
    # Galing na sa totoong DB mo, hindi na sa localStorage
    reports = DetectionResult.objects.all().order_by('-created_at')[:10]
    total_reports = DetectionResult.objects.count()
    total_users = User.objects.count()

    return render(request, 'dashboard.html', {
        'reports': reports,
        'total_reports': total_reports,
        'total_users': total_users,
        'db_reports': reports,
    })

@login_required
def reports(request):
    all_reports = DetectionResult.objects.all().order_by('-created_at')
    return render(request, 'reports.html', {
        'reports': all_reports,
        'db_reports': all_reports,
    })

@login_required
def users(request):
    all_users = User.objects.all()
    return render(request, 'users.html', {
        'users': all_users,
        'db_users': all_users,
    })

@login_required
def windmap(request):
    return render(request, 'windmap.html')

@login_required
def schoolmap(request):
    return render(request, 'schoolmap.html')

@login_required
def settings_view(request):
    return render(request, 'settings.html')