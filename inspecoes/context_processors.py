from .models import PortalUserAccess


def portal_user_context(request):
    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated:
        return {
            'portal_user_can_edit': False,
            'portal_user_can_edit_forms': False,
            'portal_can_view_forms': False,
            'portal_can_view_history': False,
            'portal_can_view_deadlines': False,
            'portal_user_name': '',
            'portal_user_registration': '',
            'portal_user_access_label': '',
        }

    access = PortalUserAccess.for_user(user)
    display_name = user.get_full_name().strip() or user.username
    registration = access.registration_display if access else user.username
    can_edit_forms = access.can_edit_forms_portal if access else user.is_superuser
    can_view_forms = access.can_view_forms_portal if access else user.is_superuser
    can_view_history = access.can_view_history_portal if access else user.is_superuser
    can_view_deadlines = access.can_view_deadlines_portal if access else user.is_superuser
    access_label = access.access_label if access else ('Editor' if user.is_superuser else 'Sem acesso')
    return {
        'portal_user_can_edit': can_edit_forms,
        'portal_user_can_edit_forms': can_edit_forms,
        'portal_can_view_forms': can_view_forms,
        'portal_can_view_history': can_view_history,
        'portal_can_view_deadlines': can_view_deadlines,
        'portal_user_name': display_name,
        'portal_user_registration': registration,
        'portal_user_access_label': access_label,
    }
