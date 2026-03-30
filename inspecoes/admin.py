from django.contrib import admin, messages
from django.contrib.auth import get_user_model

from .models import (
    Equipment,
    EquipmentFormCriteria,
    FormSubmission,
    InspectionFormType,
    PortalNotification,
    PortalUserAccess,
    VolumeStandard,
)


class EquipmentFormCriteriaInline(admin.TabularInline):
    model = EquipmentFormCriteria
    extra = 0
    autocomplete_fields = ('form_type',)
    fields = (
        'form_type',
        'acceptance_criterion_value',
        'acceptance_criterion_unit',
        'updated_at',
    )
    readonly_fields = ('updated_at',)


@admin.register(InspectionFormType)
class InspectionFormTypeAdmin(admin.ModelAdmin):
    list_display = ('code', 'title', 'active')
    search_fields = ('code', 'title', 'description')
    list_filter = ('active',)
    ordering = ('code',)


@admin.register(Equipment)
class EquipmentAdmin(admin.ModelAdmin):
    list_display = (
        'tag',
        'description',
        'location',
        'enabled_form_types_admin',
        'revisit_interval_days',
        'acceptance_criterion_admin',
        'deadline_status_admin',
        'next_visit_due_date_admin',
        'active',
    )
    search_fields = ('tag', 'description', 'location')
    list_filter = ('active', 'acceptance_criterion_unit')
    readonly_fields = ('deadline_info_admin',)
    filter_horizontal = ('inspection_form_types',)
    fields = (
        'tag',
        'description',
        'location',
        'inspection_form_types',
        'active',
        'revisit_interval_days',
        'acceptance_criterion_pct',
        'acceptance_criterion_unit',
        'notification_emails',
        'deadline_info_admin',
    )
    inlines = (EquipmentFormCriteriaInline,)

    @admin.display(description='Status do prazo')
    def deadline_status_admin(self, obj):
        return obj.deadline_status_label

    @admin.display(description='Próxima visita')
    def next_visit_due_date_admin(self, obj):
        return obj.next_visit_due_date or '-'

    @admin.display(description='Formulários habilitados')
    def enabled_form_types_admin(self, obj):
        labels = [form_type.code for form_type in obj.available_form_types]
        return ', '.join(labels) if labels else '-'

    @admin.display(description='Critério padrão')
    def acceptance_criterion_admin(self, obj):
        return obj.acceptance_criterion_display

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
    list_display = (
        'id',
        'form_type',
        'equipment',
        'om_number',
        'created_by',
        'status',
        'sap_status',
        'created_at',
    )
    list_filter = ('form_type', 'status', 'sap_status', 'created_at')
    search_fields = ('om_number', 'equipment__tag', 'equipment__description', 'executor_name')


@admin.register(EquipmentFormCriteria)
class EquipmentFormCriteriaAdmin(admin.ModelAdmin):
    list_display = (
        'equipment',
        'form_type',
        'acceptance_criterion_value',
        'acceptance_criterion_unit',
        'updated_at',
    )
    list_filter = (
        'acceptance_criterion_unit',
        'form_type',
        'equipment__active',
    )
    search_fields = ('equipment__tag', 'equipment__description', 'form_type__code', 'form_type__title')
    autocomplete_fields = ('equipment', 'form_type')
    ordering = ('equipment__tag', 'form_type__code')


@admin.register(VolumeStandard)
class VolumeStandardAdmin(admin.ModelAdmin):
    list_display = ('tag', 'description', 'nominal_volume_l', 'graduation_l', 'active')
    list_filter = ('active',)
    search_fields = ('tag', 'description')
    ordering = ('tag',)


@admin.register(PortalNotification)
class PortalNotificationAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'category', 'title', 'is_read', 'created_at', 'email_sent_at')
    list_filter = ('category', 'is_read', 'created_at')
    search_fields = ('title', 'message', 'user__username', 'user__first_name', 'user__last_name')
    autocomplete_fields = ('user', 'submission', 'equipment')
    ordering = ('-created_at',)
    readonly_fields = ('created_at', 'updated_at', 'email_sent_at')


@admin.register(PortalUserAccess)
class PortalUserAccessAdmin(admin.ModelAdmin):
    list_display = (
        'username_admin',
        'full_name_admin',
        'registration_display_admin',
        'role',
        'validator_deadline_days_admin',
        'equipment_scope_admin',
        'can_create_admin',
        'can_validate_admin',
        'can_manage_admin',
        'updated_at',
    )
    search_fields = ('user__username', 'user__first_name', 'user__last_name', 'registration')
    list_filter = ('role', 'updated_at')
    ordering = ('user__username',)
    list_per_page = 50
    autocomplete_fields = ('user',)
    filter_horizontal = ('visible_equipments',)
    actions = (
        'set_role_technician',
        'set_role_validator',
        'set_role_viewer',
        'set_role_master',
    )
    fields = (
        'user',
        'registration',
        'role',
        'validator_deadline_days',
        'visible_equipments',
        'legacy_flags_info',
    )
    readonly_fields = ('legacy_flags_info',)

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

    @admin.display(description='Prazo validador (dias)', ordering='validator_deadline_days')
    def validator_deadline_days_admin(self, obj):
        if obj.role not in {PortalUserAccess.Role.VALIDATOR, PortalUserAccess.Role.MASTER}:
            return '-'
        if obj.validator_deadline_days:
            return obj.validator_deadline_days
        return f'Padrão ({obj.validator_deadline_days_effective})'

    @admin.display(description='Pode criar/editar')
    def can_create_admin(self, obj):
        return 'Sim' if obj.can_create_forms_portal else 'Não'

    @admin.display(description='Pode validar/SAP')
    def can_validate_admin(self, obj):
        return 'Sim' if (obj.can_validate_forms_portal or obj.can_send_sap_portal) else 'Não'

    @admin.display(description='Master/Admin')
    def can_manage_admin(self, obj):
        return 'Sim' if obj.can_manage_admin_portal else 'Não'

    @admin.display(description='Escopo de equipamentos')
    def equipment_scope_admin(self, obj):
        if obj.role != PortalUserAccess.Role.TECHNICIAN:
            return 'Todos (não técnico)'
        count = obj.visible_equipments.count()
        if count == 0:
            return 'Todos'
        return f'{count} equipamento(s)'

    @admin.display(description='Flags legadas')
    def legacy_flags_info(self, obj):
        return (
            'Flags legadas permanecem no banco apenas para compatibilidade: '
            f'can_view_forms={obj.can_view_forms}, '
            f'can_view_history={obj.can_view_history}, '
            f'can_view_deadlines={obj.can_view_deadlines}, '
            f'can_edit_forms={obj.can_edit_forms}, '
            f'can_edit={obj.can_edit}.'
        )

    def _bulk_set_role(self, request, queryset, role, label):
        updated = queryset.exclude(role=role).update(role=role)
        if role == PortalUserAccess.Role.MASTER:
            user_ids = list(queryset.values_list('user_id', flat=True))
            get_user_model().objects.filter(pk__in=user_ids).update(
                is_staff=True,
                is_superuser=True,
            )
        self.message_user(
            request,
            f'{updated} usuário(s) atualizado(s) para perfil {label}.',
            level=messages.SUCCESS,
        )

    @admin.action(description='Definir perfil: Técnico')
    def set_role_technician(self, request, queryset):
        self._bulk_set_role(request, queryset, PortalUserAccess.Role.TECHNICIAN, 'Técnico')

    @admin.action(description='Definir perfil: Validador')
    def set_role_validator(self, request, queryset):
        self._bulk_set_role(request, queryset, PortalUserAccess.Role.VALIDATOR, 'Validador')

    @admin.action(description='Definir perfil: Visualizador')
    def set_role_viewer(self, request, queryset):
        self._bulk_set_role(request, queryset, PortalUserAccess.Role.VIEWER, 'Visualizador')

    @admin.action(description='Definir perfil: Master')
    def set_role_master(self, request, queryset):
        self._bulk_set_role(request, queryset, PortalUserAccess.Role.MASTER, 'Master')

