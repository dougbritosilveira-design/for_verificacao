from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from inspecoes import views as inspecoes_views

urlpatterns = [
    path('login/', auth_views.LoginView.as_view(template_name='auth/login.html', redirect_authenticated_user=True), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path(
        'senha/alterar/',
        auth_views.PasswordChangeView.as_view(
            template_name='auth/password_change_form.html',
            success_url='/senha/alterada/',
        ),
        name='password_change',
    ),
    path(
        'senha/alterada/',
        auth_views.PasswordChangeDoneView.as_view(template_name='auth/password_change_done.html'),
        name='password_change_done',
    ),
    path('admin/', admin.site.urls),
    path('', inspecoes_views.home_view, name='home'),
    path('', include('inspecoes.urls')),
]
