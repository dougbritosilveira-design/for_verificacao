from .models import PortalNotification, PortalUserAccess


def _first_last_name_or_username(user):
    first_name = (getattr(user, 'first_name', '') or '').strip()
    last_name = (getattr(user, 'last_name', '') or '').strip()
    if first_name and last_name:
        return f'{first_name} {last_name}'
    if first_name:
        return first_name
    if last_name:
        return last_name

    full_name = user.get_full_name().strip()
    if full_name:
        parts = full_name.split()
        if len(parts) >= 2:
            return f'{parts[0]} {parts[-1]}'
        return parts[0]

    return user.username


def portal_user_context(request):
    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated:
        return {
            'portal_user_can_edit': False,
            'portal_user_can_edit_forms': False,
            'portal_user_can_validate_forms': False,
            'portal_user_can_send_sap': False,
            'portal_user_can_create_forms': False,
            'portal_can_view_forms': False,
            'portal_can_view_history': False,
            'portal_can_view_deadlines': False,
            'portal_can_view_notifications': False,
            'portal_user_can_manage_admin': False,
            'portal_user_name': '',
            'portal_user_registration': '',
            'portal_user_access_label': '',
            'portal_notifications_unread_count': 0,
        }

    access = PortalUserAccess.for_user(user)
    display_name = _first_last_name_or_username(user)
    registration = access.registration_display if access else user.username
    can_create_forms = access.can_create_forms_portal if access else user.is_superuser
    can_edit_forms = access.can_edit_forms_portal if access else user.is_superuser
    can_validate_forms = access.can_validate_forms_portal if access else user.is_superuser
    can_send_sap = access.can_send_sap_portal if access else user.is_superuser
    can_view_forms = access.can_view_forms_portal if access else user.is_superuser
    can_view_history = access.can_view_history_portal if access else user.is_superuser
    can_view_deadlines = access.can_view_deadlines_portal if access else user.is_superuser
    can_view_notifications = access.can_view_notifications_portal if access else user.is_superuser
    can_manage_admin = access.can_manage_admin_portal if access else user.is_superuser
    access_label = access.access_label if access else ('Master' if user.is_superuser else 'Sem acesso')
    unread_count = PortalNotification.objects.filter(user=user, is_read=False).count()
    return {
        'portal_user_can_edit': can_edit_forms,
        'portal_user_can_edit_forms': can_edit_forms,
        'portal_user_can_validate_forms': can_validate_forms,
        'portal_user_can_send_sap': can_send_sap,
        'portal_user_can_create_forms': can_create_forms,
        'portal_can_view_forms': can_view_forms,
        'portal_can_view_history': can_view_history,
        'portal_can_view_deadlines': can_view_deadlines,
        'portal_can_view_notifications': can_view_notifications,
        'portal_user_can_manage_admin': can_manage_admin,
        'portal_user_name': display_name,
        'portal_user_registration': registration,
        'portal_user_access_label': access_label,
        'portal_notifications_unread_count': unread_count,
    }
