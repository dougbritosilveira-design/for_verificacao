from django.contrib import messages
from django.shortcuts import redirect

from .models import PortalUserAccess


class PortalAdminGuardMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith('/admin'):
            user = getattr(request, 'user', None)
            if user and user.is_authenticated:
                access = PortalUserAccess.for_user(user)
                can_manage_admin = access.can_manage_admin_portal if access else user.is_superuser
                if not can_manage_admin:
                    messages.warning(request, 'Seu perfil não possui acesso à área administrativa.')
                    return redirect('home')
        return self.get_response(request)
