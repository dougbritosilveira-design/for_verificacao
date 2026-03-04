from django.contrib import admin, messages
from django.db.models import Q

from .models import Equipment, FormSubmission, PortalUserAccess


@admin.register(Equipment)
class EquipmentAdmin(admin.ModelAdmin):
    list_display = (
        'tag',
        'description',
        'location',
        'revisit_interval_days',
        'acceptance_criterion_pct',
        'expanded_uncertainty_pct',
        'deadline_status_admin',
        'next_visit_due_date_admin',
        'active',
    )
    search_fields = ('tag', 'description', 'location')
    list_filter = ('active',)
    readonly_fields = ('deadline_info_admin',)
    fields = (
        'tag',
        'description',
        'location',
        'active',
        'revisit_interval_days',
        'acceptance_criterion_pct',
        'expanded_uncertainty_pct',
        'notification_emails',
        'deadline_info_admin',
    )

    @admin.display(description='Status do prazo')
    def deadline_status_admin(self, obj):
        return obj.deadline_status_label

    @admin.display(description='Próxima visita')
    def next_visit_due_date_admin(self, obj):
        return obj.next_visit_due_date or '-'

    @admin.display(description='Resumo do prazo')
    def deadline_info_admin(self, obj):
        if not obj.pk:
            return 'Salve o equipamento para visualizar o prazo.'
        return (
            f'Última visita: {obj.last_visit_date or "-"} | '
            f'Próxima visita: {obj.next_visit_due_date or "-"} | '
            f'Status: {obj.deadline_status_label} ({obj.deadline_status_detail})'
        )


@admin.register(FormSubmission)
class FormSubmissionAdmin(admin.ModelAdmin):
    list_display = ('id', 'equipment', 'om_number', 'status', 'sap_status', 'created_at')
    list_filter = ('status', 'sap_status', 'created_at')
    search_fields = ('om_number', 'equipment__tag', 'equipment__description')


class AccessProfileFilter(admin.SimpleListFilter):
    title = 'Perfil de acesso'
    parameter_name = 'perfil_acesso'

    def lookups(self, request, model_admin):
        return [
            ('editor', 'Editor'),
            ('readonly', 'Somente leitura'),
            ('no_access', 'Sem acesso'),
        ]

    def queryset(self, request, queryset):
        value = self.value()
        if value == 'editor':
            return queryset.filter(Q(can_edit_forms=True) | Q(can_edit=True) | Q(user__is_superuser=True))
        if value == 'readonly':
            return queryset.filter(
                Q(can_edit_forms=False),
                Q(can_edit=False),
                Q(user__is_superuser=False),
            ).filter(
                Q(can_view_forms=True) | Q(can_view_history=True) | Q(can_view_deadlines=True)
            )
        if value == 'no_access':
            return queryset.filter(
                can_view_forms=False,
                can_view_history=False,
                can_view_deadlines=False,
                user__is_superuser=False,
            )
        return queryset


@admin.register(PortalUserAccess)
class PortalUserAccessAdmin(admin.ModelAdmin):
    list_display = (
        'username_admin',
        'full_name_admin',
        'registration_display_admin',
        'can_view_forms',
        'can_view_history',
        'can_view_deadlines',
        'access_label_admin',
        'updated_at',
    )
    search_fields = ('user__username', 'user__first_name', 'user__last_name', 'registration')
    list_filter = (
        AccessProfileFilter,
        'can_view_forms',
        'can_view_history',
        'can_view_deadlines',
        'updated_at',
    )
    ordering = ('user__username',)
    list_per_page = 50
    autocomplete_fields = ('user',)
    actions = ('mark_as_editor', 'mark_as_readonly')
    fields = (
        'user',
        'registration',
        'can_view_forms',
        'can_view_history',
        'can_view_deadlines',
        'can_edit_forms',
        'can_edit',
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('user')

    @admin.display(description='Usuário', ordering='user__username')
    def username_admin(self, obj):
        return obj.user.username

    @admin.display(description='Nome', ordering='user__first_name')
    def full_name_admin(self, obj):
        full_name = obj.user.get_full_name().strip()
        return full_name or '-'

    @admin.display(description='Matrícula')
    def registration_display_admin(self, obj):
        return obj.registration_display

    @admin.display(description='Perfil')
    def access_label_admin(self, obj):
        return obj.access_label

    @admin.action(description='Definir como Editor (ação em massa)')
    def mark_as_editor(self, request, queryset):
        updated = queryset.exclude(can_edit_forms=True, can_edit=True).update(
            can_edit_forms=True,
            can_edit=True,
        )
        self.message_user(
            request,
            f'{updated} usuário(s) atualizado(s) para Editor.',
            level=messages.SUCCESS,
        )

    @admin.action(description='Definir como Somente leitura (ação em massa)')
    def mark_as_readonly(self, request, queryset):
        editable_queryset = queryset.exclude(user__is_superuser=True)
        updated = editable_queryset.exclude(can_edit_forms=False, can_edit=False).update(
            can_edit_forms=False,
            can_edit=False,
        )
        skipped_superusers = queryset.filter(user__is_superuser=True).count()
        self.message_user(
            request,
            f'{updated} usuário(s) atualizado(s) para Somente leitura.',
            level=messages.SUCCESS,
        )
        if skipped_superusers:
            self.message_user(
                request,
                f'{skipped_superusers} superusuário(s) não foram alterados.',
                level=messages.WARNING,
            )
