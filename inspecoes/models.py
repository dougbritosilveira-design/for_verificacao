import re
from datetime import datetime, timedelta
from decimal import Decimal, localcontext

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone
from django.utils.functional import cached_property

CRITERION_UNIT_CHOICES = [
    ('%', '%'),
    ('m', 'm'),
    ('mm', 'mm'),
]


class PortalUserAccess(models.Model):
    class Role(models.TextChoices):
        TECHNICIAN = 'technician', 'Técnico'
        VALIDATOR = 'validator', 'Validador'
        VIEWER = 'viewer', 'Visualizador'
        MASTER = 'master', 'Master'

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='portal_access',
        verbose_name='Usuário',
    )
    registration = models.CharField(
        'Matrícula',
        max_length=50,
        blank=True,
        help_text='Se vazio, usa o username do usuário.',
    )
    role = models.CharField(
        'Perfil',
        max_length=20,
        choices=Role.choices,
        default=Role.VIEWER,
        help_text='Perfil operacional no portal: Técnico, Validador, Visualizador ou Master.',
    )
    validator_deadline_days = models.PositiveIntegerField(
        'Prazo para validação (dias)',
        null=True,
        blank=True,
        validators=[MinValueValidator(1)],
        help_text=(
            'Prazo em dias para validar formulários atribuídos a este usuário '
            '(perfil Validador). Se vazio, usa o padrão do sistema.'
        ),
    )
    visible_equipments = models.ManyToManyField(
        'Equipment',
        blank=True,
        related_name='portal_user_accesses',
        verbose_name='Equipamentos visíveis (técnico)',
        help_text=(
            'Para perfil Técnico, selecione os equipamentos que este usuário pode enxergar. '
            'Se deixar vazio, o técnico enxerga todos.'
        ),
    )
    can_view_forms = models.BooleanField(
        'Acessar tela Formulários (legado)',
        default=True,
        help_text='Compatibilidade com versões anteriores.',
    )
    can_view_history = models.BooleanField(
        'Acessar tela Histórico (legado)',
        default=True,
        help_text='Compatibilidade com versões anteriores.',
    )
    can_view_deadlines = models.BooleanField(
        'Acessar tela Prazos (legado)',
        default=True,
        help_text='Compatibilidade com versões anteriores.',
    )
    can_edit_forms = models.BooleanField(
        'Editar formulários (legado)',
        default=False,
        help_text='Compatibilidade com versões anteriores.',
    )
    can_edit = models.BooleanField(
        'Pode editar (legado)',
        default=False,
        help_text='Compatibilidade com versões anteriores.',
    )
    updated_at = models.DateTimeField('Atualizado em', auto_now=True)

    class Meta:
        verbose_name = 'Acesso ao portal'
        verbose_name_plural = 'Acessos ao portal'
        ordering = ['user__username']

    def __str__(self):
        return f'{self.user.username} ({self.access_label})'

    @property
    def registration_display(self):
        return self.registration or self.user.username

    @property
    def is_master_portal(self):
        return self.role == self.Role.MASTER

    @property
    def can_view_forms_portal(self):
        if self.is_master_portal:
            return True
        if self.role in {self.Role.TECHNICIAN, self.Role.VALIDATOR, self.Role.VIEWER}:
            return True
        return self.can_view_forms

    @property
    def can_view_history_portal(self):
        if self.is_master_portal:
            return True
        if self.role in {self.Role.TECHNICIAN, self.Role.VALIDATOR, self.Role.VIEWER}:
            return True
        return self.can_view_history

    @property
    def can_view_deadlines_portal(self):
        if self.is_master_portal:
            return True
        if self.role in {self.Role.TECHNICIAN, self.Role.VALIDATOR, self.Role.VIEWER}:
            return True
        return self.can_view_deadlines

    @property
    def can_create_forms_portal(self):
        return self.is_master_portal or self.role == self.Role.TECHNICIAN or self.can_edit_forms or self.can_edit

    @property
    def can_edit_forms_portal(self):
        return self.can_create_forms_portal

    @property
    def can_validate_forms_portal(self):
        return self.is_master_portal or self.role == self.Role.VALIDATOR

    @property
    def can_send_sap_portal(self):
        return self.is_master_portal or self.role == self.Role.VALIDATOR

    @property
    def can_view_notifications_portal(self):
        return self.is_master_portal or self.role in {
            self.Role.TECHNICIAN,
            self.Role.VALIDATOR,
            self.Role.VIEWER,
        }

    @property
    def can_receive_deadline_notifications_portal(self):
        return self.is_master_portal or self.role == self.Role.TECHNICIAN

    @property
    def can_manage_admin_portal(self):
        return self.is_master_portal

    @property
    def can_edit_portal(self):
        return self.can_edit_forms_portal or self.can_validate_forms_portal

    @property
    def scoped_equipment_ids(self):
        if self.role != self.Role.TECHNICIAN:
            return None
        equipment_ids = list(self.visible_equipments.values_list('id', flat=True))
        return equipment_ids or None

    @property
    def access_label(self):
        if self.is_master_portal:
            return self.Role.MASTER.label
        return dict(self.Role.choices).get(self.role, 'Sem acesso')

    @staticmethod
    def default_validator_deadline_days():
        configured_days = getattr(settings, 'PORTAL_VALIDATOR_DEADLINE_DAYS', 2)
        try:
            parsed = int(configured_days)
        except (TypeError, ValueError):
            parsed = 2
        return max(parsed, 1)

    @property
    def validator_deadline_days_effective(self):
        if self.validator_deadline_days:
            return int(self.validator_deadline_days)
        return self.default_validator_deadline_days()

    @classmethod
    def for_user(cls, user):
        if not user or not user.is_authenticated:
            return None
        defaults = {
            'registration': user.username,
            'role': cls.Role.MASTER if user.is_superuser else cls.Role.VIEWER,
        }
        access, _ = cls.objects.get_or_create(user=user, defaults=defaults)
        return access

    def save(self, *args, **kwargs):
        if not self.registration and self.user_id:
            self.registration = self.user.username
        super().save(*args, **kwargs)
        self._sync_master_to_superuser()

    def _sync_master_to_superuser(self):
        if not self.user_id:
            return
        if self.role != self.Role.MASTER:
            return
        user_model = type(self.user)
        user_model.objects.filter(pk=self.user_id).exclude(
            is_superuser=True,
            is_staff=True,
        ).update(
            is_superuser=True,
            is_staff=True,
        )


class InspectionFormType(models.Model):
    code = models.CharField('Codigo', max_length=60, unique=True)
    title = models.CharField('Titulo', max_length=255)
    description = models.TextField('Descricao', blank=True)
    active = models.BooleanField('Ativo', default=True)

    class Meta:
        ordering = ['code']
        verbose_name = 'Tipo de formulario'
        verbose_name_plural = 'Tipos de formulario'

    def __str__(self):
        return self.full_label

    @property
    def full_label(self):
        return f'{self.code} - {self.title}'

    @classmethod
    def default_code(cls):
        return 'FOR 08.05.003'

    @classmethod
    def default_title(cls):
        return 'Verificacao e ajuste de balanca dinamica (MVP)'

    @classmethod
    def default_label(cls):
        return f'{cls.default_code()} - {cls.default_title()}'


class Equipment(models.Model):
    tag = models.CharField('TAG', max_length=80, unique=True)
    description = models.CharField('Descrição', max_length=255)
    location = models.CharField('Local', max_length=255)
    inspection_form_types = models.ManyToManyField(
        InspectionFormType,
        blank=True,
        related_name='equipments',
        verbose_name='Formularios habilitados',
        help_text='Selecione os formularios que podem ser aplicados neste equipamento.',
    )
    revisit_interval_days = models.PositiveIntegerField(
        'Periodicidade da nova visita (dias)',
        null=True,
        blank=True,
        validators=[MinValueValidator(1)],
        help_text='Quantidade de dias para nova verificação/ajuste do equipamento.',
    )
    notification_emails = models.TextField(
        'E-mails para notificação',
        blank=True,
        help_text='Informe um ou mais e-mails separados por vírgula, ponto e vírgula ou quebra de linha.',
    )
    acceptance_criterion_pct = models.DecimalField(
        'Critério padrão',
        max_digits=6,
        decimal_places=3,
        default=Decimal('1.0'),
        validators=[MinValueValidator(Decimal('0.001'))],
        help_text='Limite padrão de aceitação do equipamento. Ex.: 1,0 (%, m ou mm).',
    )
    acceptance_criterion_unit = models.CharField(
        'Unidade do critério padrão',
        max_length=8,
        choices=CRITERION_UNIT_CHOICES,
        default='%',
        help_text='Unidade do critério padrão do equipamento (% , m ou mm).',
    )
    expanded_uncertainty_pct = models.DecimalField(
        'Incerteza expandida (%)',
        max_digits=6,
        decimal_places=3,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal('0.000'))],
        help_text='Opcional.',
    )
    active = models.BooleanField('Ativo', default=True)

    class Meta:
        ordering = ['tag']

    def __str__(self):
        return f'{self.tag} - {self.description}'

    @property
    def available_form_types(self):
        return self.inspection_form_types.filter(active=True).order_by('code')

    @property
    def acceptance_criterion_display(self):
        if self.acceptance_criterion_pct is None:
            return '-'
        unit = self.acceptance_criterion_unit or '%'
        decimals = 2 if unit == '%' else 3
        with localcontext() as ctx:
            ctx.prec = 18
            quant = Decimal('1').scaleb(-decimals)
            value = Decimal(str(self.acceptance_criterion_pct)).quantize(quant)
        return f'{value:.{decimals}f}{unit}'.replace('.', ',')

    def criteria_for_form(self, form_type):
        if not form_type:
            return None
        return self.form_criteria_configs.filter(form_type=form_type).first()

    @property
    def deadline_warning_days(self):
        return int(getattr(settings, 'EQUIPMENT_DUE_SOON_DAYS', 7))

    @cached_property
    def last_completed_submission(self):
        return (
            self.submissions.filter(
                status__in=[
                    FormSubmission.Status.APPROVED,
                    FormSubmission.Status.SENT_TO_SAP,
                ]
            )
            .order_by('-execution_date', '-validated_at', '-created_at')
            .first()
        )

    @property
    def last_visit_date(self):
        submission = self.last_completed_submission
        if not submission:
            return None
        if submission.execution_date:
            return submission.execution_date
        if submission.validated_at:
            return timezone.localtime(submission.validated_at).date()
        return None

    @property
    def next_visit_due_date(self):
        if not self.revisit_interval_days or not self.last_visit_date:
            return None
        return self.last_visit_date + timedelta(days=self.revisit_interval_days)

    @property
    def days_until_due(self):
        due_date = self.next_visit_due_date
        if not due_date:
            return None
        return (due_date - timezone.localdate()).days

    @property
    def deadline_status_code(self):
        if not self.revisit_interval_days:
            return 'not_configured'
        if not self.last_visit_date:
            return 'no_history'
        if self.days_until_due is None:
            return 'no_history'
        if self.days_until_due < 0:
            return 'overdue'
        if self.days_until_due <= self.deadline_warning_days:
            return 'due_soon'
        return 'on_time'

    @property
    def deadline_status_label(self):
        labels = {
            'not_configured': 'Não configurado',
            'no_history': 'Sem histórico',
            'on_time': 'Dentro do prazo',
            'due_soon': 'Próximo do vencimento',
            'overdue': 'Vencido / atrasado',
        }
        return labels.get(self.deadline_status_code, 'Indefinido')

    @property
    def deadline_badge_class(self):
        classes = {
            'not_configured': 'pending',
            'no_history': 'pending',
            'on_time': 'ok',
            'due_soon': 'warn',
            'overdue': 'fail',
        }
        return classes.get(self.deadline_status_code, 'pending')

    @property
    def deadline_status_detail(self):
        status = self.deadline_status_code
        if status == 'not_configured':
            return 'Defina a periodicidade em dias no cadastro do equipamento.'
        if status == 'no_history':
            return 'Ainda não há formulário salvo/validado para calcular a próxima visita.'
        if self.days_until_due is None:
            return 'Sem prazo calculado.'
        if self.days_until_due < 0:
            return f'Atrasado há {abs(self.days_until_due)} dia(s).'
        if self.days_until_due == 0:
            return 'Vence hoje.'
        return f'Faltam {self.days_until_due} dia(s).'

    @property
    def notification_recipients(self):
        if not self.notification_emails:
            return []
        parts = re.split(r'[;,\n\r]+', self.notification_emails)
        return [p.strip() for p in parts if p.strip()]

    @property
    def has_notification_recipients(self):
        return bool(self.notification_recipients)

    @property
    def should_notify_deadline(self):
        return self.deadline_status_code in {'due_soon', 'overdue'} and self.has_notification_recipients


class EquipmentFormCriteria(models.Model):
    class Unit(models.TextChoices):
        PERCENT = '%', '%'
        METER = 'm', 'm'
        MILLIMETER = 'mm', 'mm'

    equipment = models.ForeignKey(
        Equipment,
        on_delete=models.CASCADE,
        related_name='form_criteria_configs',
        verbose_name='Equipamento',
    )
    form_type = models.ForeignKey(
        InspectionFormType,
        on_delete=models.CASCADE,
        related_name='equipment_criteria_configs',
        verbose_name='Formulario',
    )
    acceptance_criterion_value = models.DecimalField(
        'Criterio de aceitacao',
        max_digits=10,
        decimal_places=3,
        default=Decimal('1.0'),
        validators=[MinValueValidator(Decimal('0.000'))],
    )
    acceptance_criterion_unit = models.CharField(
        'Unidade do criterio',
        max_length=8,
        choices=Unit.choices,
        default=Unit.PERCENT,
    )
    expanded_uncertainty_value = models.DecimalField(
        'Incerteza expandida',
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal('0.000'))],
    )
    expanded_uncertainty_unit = models.CharField(
        'Unidade da incerteza',
        max_length=8,
        choices=Unit.choices,
        default=Unit.PERCENT,
    )
    updated_at = models.DateTimeField('Atualizado em', auto_now=True)

    class Meta:
        verbose_name = 'Configuracao de criterio por formulario'
        verbose_name_plural = 'Configuracoes de criterio por formulario'
        ordering = ['equipment__tag', 'form_type__code']
        constraints = [
            models.UniqueConstraint(
                fields=['equipment', 'form_type'],
                name='unique_equipment_form_criteria_config',
            )
        ]

    def __str__(self):
        return (
            f'{self.equipment.tag} | {self.form_type.code} | '
            f'criterio={self.acceptance_criterion_value}{self.acceptance_criterion_unit}'
        )

    def save(self, *args, **kwargs):
        # A referência de incerteza cadastrada foi descontinuada no fluxo.
        # Mantemos apenas a unidade para exibição e cálculo de U(e) calculada.
        self.expanded_uncertainty_value = None
        if self.acceptance_criterion_unit:
            self.expanded_uncertainty_unit = self.acceptance_criterion_unit
        super().save(*args, **kwargs)


class VolumeStandard(models.Model):
    tag = models.CharField('TAG do aferidor', max_length=80, unique=True)
    description = models.CharField('Descrição', max_length=255, blank=True)
    nominal_volume_l = models.DecimalField(
        'Volume nominal (L)',
        max_digits=12,
        decimal_places=3,
        null=True,
        blank=True,
    )
    graduation_l = models.DecimalField(
        'Graduação (L)',
        max_digits=12,
        decimal_places=4,
        default=Decimal('0.0100'),
        validators=[MinValueValidator(Decimal('0.0001'))],
        help_text='Usado no cálculo de incerteza de volume (u_volume = graduação / √12).',
    )
    active = models.BooleanField('Ativo', default=True)

    class Meta:
        verbose_name = 'Aferidor de volume'
        verbose_name_plural = 'Aferidores de volume'
        ordering = ['tag']

    def __str__(self):
        if self.description:
            return f'{self.tag} - {self.description}'
        return self.tag


class FormSubmission(models.Model):
    DEFAULT_ACCEPTANCE_LIMIT_PCT = Decimal('1.0')
    DEFAULT_UNCERTAINTY_TOTALIZER_RESOLUTION = Decimal('0.001')
    DEFAULT_UNCERTAINTY_MEASUREMENT_DURATION_MIN = Decimal('5')
    DEFAULT_UNCERTAINTY_COVERAGE_FACTOR = Decimal('2')
    FORM_CODE_BELT = 'FOR 08.05.003'
    FORM_CODE_LEVEL = 'FOR 07.04.01.002'
    FORM_CODE_SCANNER = 'FOR SCANNER'
    FORM_CODE_FLOW = 'FOR VAZAO'
    FORM_CODE_FLOW_CERT = 'FOR VAZAO'
    FORM_CODE_FLOW_ADJUST = 'FOR 08.05.006'
    FORM_CODE_FLOW_ADJUST_ALT = 'FOR 08.03.006'
    FORM_CODE_DENSITY = 'FOR 08.03.003'

    class Status(models.TextChoices):
        DRAFT = 'draft', 'Rascunho'
        PENDING_VALIDATION = 'pending_validation', 'Pendente validação'
        REWORK_REQUIRED = 'rework_required', 'Refação solicitada'
        APPROVED = 'approved', 'Aprovado'
        SENT_TO_SAP = 'sent_to_sap', 'Enviado SAP'

    class SapStatus(models.TextChoices):
        NOT_STARTED = 'not_started', 'Não iniciado'
        SUCCESS = 'success', 'Sucesso'
        FAILED = 'failed', 'Falhou'

    equipment = models.ForeignKey(Equipment, on_delete=models.PROTECT, related_name='submissions')
    form_type = models.ForeignKey(
        InspectionFormType,
        on_delete=models.PROTECT,
        related_name='submissions',
        verbose_name='Formulario',
        null=True,
        blank=True,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_submissions',
        verbose_name='Criado por',
    )
    assigned_validator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assigned_submissions_to_validate',
        verbose_name='Validador designado',
    )
    location_snapshot = models.CharField(max_length=255)
    om_number = models.CharField('Nº OM', max_length=50)
    execution_date = models.DateField(default=timezone.localdate)
    executor_name = models.CharField(max_length=120)
    acceptance_criterion_pct = models.DecimalField(
        'Critério de aceitação (%)',
        max_digits=6,
        decimal_places=3,
        default=Decimal('1.0'),
    )
    acceptance_criterion_unit = models.CharField(
        'Unidade do criterio',
        max_length=8,
        choices=EquipmentFormCriteria.Unit.choices,
        default=EquipmentFormCriteria.Unit.PERCENT,
    )
    expanded_uncertainty_pct = models.DecimalField(
        'Incerteza expandida (%)',
        max_digits=6,
        decimal_places=3,
        null=True,
        blank=True,
    )
    expanded_uncertainty_unit = models.CharField(
        'Unidade da incerteza',
        max_length=8,
        choices=EquipmentFormCriteria.Unit.choices,
        default=EquipmentFormCriteria.Unit.PERCENT,
    )
    expanded_uncertainty_calc_pct = models.DecimalField(
        'Incerteza expandida calculada (%)',
        max_digits=12,
        decimal_places=3,
        null=True,
        blank=True,
    )

    scanner_certificate_file = models.FileField(
        upload_to='scanner_certificates/',
        null=True,
        blank=True,
        verbose_name='Certificado de calibração (PDF)',
    )
    scanner_certificate_number = models.CharField(max_length=120, blank=True)
    scanner_provider = models.CharField(max_length=255, blank=True)
    scanner_model = models.CharField(max_length=255, blank=True)
    scanner_serial_number = models.CharField(max_length=120, blank=True)
    scanner_measurement_date = models.DateField(null=True, blank=True)
    scanner_release_date = models.DateField(null=True, blank=True)
    scanner_manufacturer_ppm = models.DecimalField(
        max_digits=8,
        decimal_places=3,
        null=True,
        blank=True,
        default=Decimal('10'),
    )
    scanner_k_factor = models.DecimalField(
        max_digits=8,
        decimal_places=3,
        null=True,
        blank=True,
        default=Decimal('2'),
    )
    scanner_u_ref_mm = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True, default=Decimal('0'))
    scanner_u_rep_mm = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    scanner_u_res_mm = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True, default=Decimal('0'))
    scanner_u_setup_mm = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True, default=Decimal('0'))
    scanner_u_env_mm = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True, default=Decimal('0'))
    scanner_target_1 = models.CharField(max_length=120, blank=True, default='Refletor 1')
    scanner_nominal_1_m = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    scanner_measured_1_m = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    scanner_target_2 = models.CharField(max_length=120, blank=True, default='Refletor 2')
    scanner_nominal_2_m = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    scanner_measured_2_m = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    scanner_target_3 = models.CharField(max_length=120, blank=True, default='Refletor 3')
    scanner_nominal_3_m = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    scanner_measured_3_m = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    scanner_target_4 = models.CharField(max_length=120, blank=True, default='Refletor 4')
    scanner_nominal_4_m = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    scanner_measured_4_m = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    scanner_target_5 = models.CharField(max_length=120, blank=True, default='Refletor 5')
    scanner_nominal_5_m = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    scanner_measured_5_m = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    scanner_target_6 = models.CharField(max_length=120, blank=True, default='Refletor 6')
    scanner_nominal_6_m = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    scanner_measured_6_m = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)

    flow_certificate_file = models.FileField(
        upload_to='flow_certificates/',
        null=True,
        blank=True,
        verbose_name='Certificado de calibração (PDF)',
    )
    flow_certificate_number = models.CharField(max_length=120, blank=True)
    flow_provider = models.CharField(max_length=255, blank=True)
    flow_meter_model = models.CharField(max_length=255, blank=True)
    flow_meter_serial_number = models.CharField(max_length=120, blank=True)
    flow_converter_model = models.CharField(max_length=255, blank=True)
    flow_converter_serial_number = models.CharField(max_length=120, blank=True)
    flow_tag_on_certificate = models.CharField(max_length=120, blank=True)
    flow_measurement_date = models.DateField(null=True, blank=True)
    flow_release_date = models.DateField(null=True, blank=True)
    flow_calibration_range_min_m3h = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    flow_calibration_range_max_m3h = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)

    flow_point_label_1 = models.CharField(max_length=120, blank=True, default='Ponto 1')
    flow_calibration_1_m3h = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    flow_indicated_1_m3h = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    flow_reference_1_m3h = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    flow_tendency_1_pct = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    flow_uncertainty_1_pct = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    flow_k_1 = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)

    flow_point_label_2 = models.CharField(max_length=120, blank=True, default='Ponto 2')
    flow_calibration_2_m3h = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    flow_indicated_2_m3h = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    flow_reference_2_m3h = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    flow_tendency_2_pct = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    flow_uncertainty_2_pct = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    flow_k_2 = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)

    flow_point_label_3 = models.CharField(max_length=120, blank=True, default='Ponto 3')
    flow_calibration_3_m3h = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    flow_indicated_3_m3h = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    flow_reference_3_m3h = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    flow_tendency_3_pct = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    flow_uncertainty_3_pct = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    flow_k_3 = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)

    flow_point_label_4 = models.CharField(max_length=120, blank=True, default='Ponto 4')
    flow_calibration_4_m3h = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    flow_indicated_4_m3h = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    flow_reference_4_m3h = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    flow_tendency_4_pct = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    flow_uncertainty_4_pct = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    flow_k_4 = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)

    flow_point_label_5 = models.CharField(max_length=120, blank=True, default='Ponto 5')
    flow_calibration_5_m3h = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    flow_indicated_5_m3h = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    flow_reference_5_m3h = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    flow_tendency_5_pct = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    flow_uncertainty_5_pct = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    flow_k_5 = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)

    flow_point_label_6 = models.CharField(max_length=120, blank=True, default='Ponto 6')
    flow_calibration_6_m3h = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    flow_indicated_6_m3h = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    flow_reference_6_m3h = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    flow_tendency_6_pct = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    flow_uncertainty_6_pct = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    flow_k_6 = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)

    flow_adjust_thickness_1_mm = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    flow_adjust_thickness_2_mm = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    flow_adjust_thickness_3_mm = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    flow_adjust_thickness_4_mm = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    flow_adjust_circumference_ci_mm = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    flow_adjust_pipe_nominal_in = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)

    flow_adjust_before_totmv_m3 = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    flow_adjust_before_totsup_m3 = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    flow_adjust_before_totmv_start_time = models.TimeField(null=True, blank=True)
    flow_adjust_before_totmv_end_time = models.TimeField(null=True, blank=True)
    flow_adjust_before_totsup_start_time = models.TimeField(null=True, blank=True)
    flow_adjust_before_totsup_end_time = models.TimeField(null=True, blank=True)

    flow_adjust_after_totmv_m3 = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    flow_adjust_after_totsup_m3 = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    flow_adjust_after_totmv_start_time = models.TimeField(null=True, blank=True)
    flow_adjust_after_totmv_end_time = models.TimeField(null=True, blank=True)
    flow_adjust_after_totsup_start_time = models.TimeField(null=True, blank=True)
    flow_adjust_after_totsup_end_time = models.TimeField(null=True, blank=True)

    flow_adjust_u_ci_mm = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        null=True,
        blank=True,
        default=Decimal('1.000'),
    )
    flow_adjust_u_inst_t_mm = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        null=True,
        blank=True,
        default=Decimal('0.200'),
    )
    flow_adjust_u_delta_t_s = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        null=True,
        blank=True,
        default=Decimal('5.000'),
    )
    flow_adjust_u_dut_repeat_pct = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        null=True,
        blank=True,
        default=Decimal('0.000'),
    )
    flow_adjust_u_dut_res_pct = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        null=True,
        blank=True,
        default=Decimal('0.000'),
    )
    flow_adjust_k_factor = models.DecimalField(
        max_digits=8,
        decimal_places=3,
        null=True,
        blank=True,
        default=Decimal('2.000'),
    )

    density_scale_equipment = models.ForeignKey(
        Equipment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='density_related_submissions',
        verbose_name='Balança estática utilizada',
    )
    density_standard_1 = models.ForeignKey(
        VolumeStandard,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='density_submission_standard_1',
        verbose_name='Aferidor 1',
    )
    density_standard_2 = models.ForeignKey(
        VolumeStandard,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='density_submission_standard_2',
        verbose_name='Aferidor 2',
    )
    density_standard_3 = models.ForeignKey(
        VolumeStandard,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='density_submission_standard_3',
        verbose_name='Aferidor 3',
    )
    density_scale_mab_kg = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    density_scale_mib_kg = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    density_scale_criterion_pct = models.DecimalField(
        max_digits=8,
        decimal_places=3,
        null=True,
        blank=True,
        default=Decimal('1.000'),
    )
    density_scale_u_additional_kg = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        null=True,
        blank=True,
        default=Decimal('0.0000'),
    )

    density_before_low_point_gcm3 = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    density_before_high_point_gcm3 = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    density_before_low_count_cts = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    density_before_high_count_cts = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)

    density_before_empty_1_kg = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    density_before_full_1_kg = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    density_before_volume_1_l = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    density_before_empty_2_kg = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    density_before_full_2_kg = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    density_before_volume_2_l = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    density_before_empty_3_kg = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    density_before_full_3_kg = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    density_before_volume_3_l = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    density_before_mds_informed_gcm3 = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    density_before_mds_reading_1_gcm3 = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    density_before_mds_reading_2_gcm3 = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    density_before_mds_reading_3_gcm3 = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    density_before_mds_reading_4_gcm3 = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    density_before_mds_reading_5_gcm3 = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)

    density_after_low_point_gcm3 = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    density_after_high_point_gcm3 = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    density_after_low_count_cts = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    density_after_high_count_cts = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)

    density_after_empty_1_kg = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    density_after_full_1_kg = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    density_after_volume_1_l = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    density_after_empty_2_kg = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    density_after_full_2_kg = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    density_after_volume_2_l = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    density_after_empty_3_kg = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    density_after_full_3_kg = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    density_after_volume_3_l = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    density_after_mds_informed_gcm3 = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    density_after_mds_reading_1_gcm3 = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    density_after_mds_reading_2_gcm3 = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    density_after_mds_reading_3_gcm3 = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    density_after_mds_reading_4_gcm3 = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    density_after_mds_reading_5_gcm3 = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)

    density_volume_graduation_l = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        null=True,
        blank=True,
        default=Decimal('0.0100'),
    )
    density_mds_resolution_gcm3 = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        null=True,
        blank=True,
        default=Decimal('0.0010'),
    )
    density_k_factor = models.DecimalField(
        max_digits=8,
        decimal_places=3,
        null=True,
        blank=True,
        default=Decimal('2.000'),
    )

    level_before_vm_1 = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    level_before_vl_1 = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    level_before_vm_2 = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    level_before_vl_2 = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    level_before_vm_3 = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    level_before_vl_3 = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    level_before_vm_4 = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    level_before_vl_4 = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    level_after_vm_1 = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    level_after_vl_1 = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    level_after_vm_2 = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    level_after_vl_2 = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    level_after_vm_3 = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    level_after_vl_3 = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    level_after_vm_4 = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    level_after_vl_4 = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    level_resolution_tape_m = models.DecimalField(
        'Resolucao da trena (m)',
        max_digits=12,
        decimal_places=4,
        null=True,
        blank=True,
        default=Decimal('0.001'),
    )
    level_resolution_instrument_m = models.DecimalField(
        'Resolucao do instrumento (m)',
        max_digits=12,
        decimal_places=4,
        null=True,
        blank=True,
        default=Decimal('0.010'),
    )
    level_coverage_factor_k = models.DecimalField(
        'Fator de abrangencia k',
        max_digits=8,
        decimal_places=3,
        null=True,
        blank=True,
        default=Decimal('2.000'),
    )

    t1 = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    t2 = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    t3 = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    m1 = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    m2 = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    m3 = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    belt_replaced = models.BooleanField('Houve troca de correia?', default=False)
    mark_distance = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)

    pulses_per_turn_1 = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    pulses_per_turn_2 = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    pulses_per_turn_3 = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    ibm = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    speed_characteristic_b04 = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True)

    abw_1 = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    abw_2 = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    abw_3 = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    tare_1 = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    tare_2 = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    tare_3 = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)

    applied_weight = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    bridge_length = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    belt_length = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    belt_speed_v = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)

    il_before_ti = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    il_before_tf = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    il_after_ti = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    il_after_tf = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)

    check_weight = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    kor = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    span_value = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    calculated_flow_ic = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    error_before_pct = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    error_after_pct = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    standards_used = models.CharField(max_length=255, blank=True)
    sector = models.CharField(max_length=120, blank=True)
    sector_2 = models.CharField(max_length=120, blank=True)
    sector_3 = models.CharField(max_length=120, blank=True)
    validator_registration = models.CharField(max_length=50, blank=True)
    technician_1_name = models.CharField(max_length=120, blank=True)
    technician_2_name = models.CharField(max_length=120, blank=True)
    technician_2_registration = models.CharField(max_length=50, blank=True)
    technician_3_name = models.CharField(max_length=120, blank=True)
    technician_3_registration = models.CharField(max_length=50, blank=True)
    observation = models.TextField(blank=True)

    validator_name = models.CharField(max_length=120, blank=True)
    validator_signature_data = models.TextField(blank=True)
    validation_feedback = models.TextField(blank=True)
    validated_at = models.DateTimeField(null=True, blank=True)
    validation_requested_at = models.DateTimeField(
        'Enviado para validação em',
        null=True,
        blank=True,
    )
    validation_due_at = models.DateTimeField(
        'Prazo de validação até',
        null=True,
        blank=True,
    )
    validation_deadline_days = models.PositiveIntegerField(
        'Prazo de validação (dias)',
        null=True,
        blank=True,
        validators=[MinValueValidator(1)],
    )

    status = models.CharField(max_length=32, choices=Status.choices, default=Status.DRAFT)

    sap_status = models.CharField(max_length=24, choices=SapStatus.choices, default=SapStatus.NOT_STARTED)
    sap_attachment_id = models.CharField(max_length=120, blank=True)
    sap_response_message = models.TextField(blank=True)
    sap_sent_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'OM {self.om_number} - {self.equipment.tag} - {self.form_type_label}'

    @property
    def form_type_label(self):
        if self.form_type_id and self.form_type:
            return self.form_type.full_label
        return InspectionFormType.default_label()

    @property
    def assigned_validator_label(self):
        if not self.assigned_validator_id or not self.assigned_validator:
            return '-'
        full_name = self.assigned_validator.get_full_name().strip() or self.assigned_validator.username
        access = getattr(self.assigned_validator, 'portal_access', None)
        registration = access.registration_display if access else self.assigned_validator.username
        return f'{full_name} ({registration})'

    @property
    def resolved_validation_deadline_days(self):
        if self.validation_deadline_days:
            return int(self.validation_deadline_days)
        if self.assigned_validator_id and self.assigned_validator:
            access = getattr(self.assigned_validator, 'portal_access', None)
            if access:
                return access.validator_deadline_days_effective
        return PortalUserAccess.default_validator_deadline_days()

    @property
    def validation_deadline_days_display(self):
        if self.validation_deadline_days:
            return int(self.validation_deadline_days)
        if self.status == self.Status.PENDING_VALIDATION or self.validation_requested_at:
            return self.resolved_validation_deadline_days
        return None

    @property
    def validation_due_at_effective(self):
        if self.validation_due_at:
            return self.validation_due_at
        if self.validation_requested_at:
            return self.validation_requested_at + timedelta(days=self.resolved_validation_deadline_days)
        return None

    @property
    def validation_requested_date_local(self):
        if not self.validation_requested_at:
            return None
        return timezone.localtime(self.validation_requested_at).date()

    @property
    def validation_due_date_local(self):
        due_at = self.validation_due_at_effective
        if not due_at:
            return None
        return timezone.localtime(due_at).date()

    @property
    def validation_deadline_days_remaining(self):
        due_date = self.validation_due_date_local
        if not due_date:
            return None
        return (due_date - timezone.localdate()).days

    @property
    def validation_deadline_status_code(self):
        due_date = self.validation_due_date_local
        if not due_date:
            if self.status == self.Status.PENDING_VALIDATION:
                return 'pending_no_deadline'
            return 'not_pending'
        if self.validated_at:
            validated_date = timezone.localtime(self.validated_at).date()
            return 'validated_on_time' if validated_date <= due_date else 'validated_overdue'
        if self.status != self.Status.PENDING_VALIDATION:
            return 'not_pending'
        days = self.validation_deadline_days_remaining
        if days is None:
            return 'pending_no_deadline'
        return 'pending_on_time' if days >= 0 else 'pending_overdue'

    @property
    def validation_deadline_status_label(self):
        labels = {
            'pending_on_time': 'Dentro do prazo',
            'pending_overdue': 'Prazo vencido',
            'validated_on_time': 'Validado no prazo',
            'validated_overdue': 'Validado em atraso',
            'pending_no_deadline': 'Sem prazo',
            'not_pending': 'Não pendente',
        }
        return labels.get(self.validation_deadline_status_code, 'Indefinido')

    @property
    def validation_deadline_badge_class(self):
        badge = {
            'pending_on_time': 'ok',
            'pending_overdue': 'fail',
            'validated_on_time': 'ok',
            'validated_overdue': 'warn',
            'pending_no_deadline': 'pending',
            'not_pending': 'pending',
        }
        return badge.get(self.validation_deadline_status_code, 'pending')

    @property
    def validation_deadline_detail(self):
        status = self.validation_deadline_status_code
        due_date = self.validation_due_date_local
        if status == 'pending_no_deadline':
            return 'Prazo de validação não configurado para este envio.'
        if status == 'not_pending':
            if due_date:
                return f'Prazo definido para {due_date.strftime("%d/%m/%Y")}.'
            return 'Ainda não enviado para validação.'
        if status == 'pending_on_time':
            remaining = self.validation_deadline_days_remaining
            if remaining == 0:
                return f'Vence hoje ({due_date.strftime("%d/%m/%Y")}).'
            return f'Faltam {remaining} dia(s). Vence em {due_date.strftime("%d/%m/%Y")}.'
        if status == 'pending_overdue':
            overdue = abs(self.validation_deadline_days_remaining or 0)
            return f'Atrasado há {overdue} dia(s). Venceu em {due_date.strftime("%d/%m/%Y")}.'
        if status == 'validated_on_time':
            return f'Validação concluída no prazo (limite {due_date.strftime("%d/%m/%Y")}).'
        if status == 'validated_overdue':
            validated_date = timezone.localtime(self.validated_at).date() if self.validated_at else None
            if validated_date:
                delay_days = (validated_date - due_date).days
                return (
                    f'Validação concluída com {delay_days} dia(s) de atraso '
                    f'(limite {due_date.strftime("%d/%m/%Y")}).'
                )
        return 'Prazo de validação calculado.'

    def schedule_validation_deadline(self, requested_at=None):
        requested_at = requested_at or timezone.now()
        deadline_days = self.resolved_validation_deadline_days
        self.validation_requested_at = requested_at
        self.validation_deadline_days = deadline_days
        self.validation_due_at = requested_at + timedelta(days=deadline_days)

    @property
    def form_code(self):
        if self.form_type_id and self.form_type:
            return (self.form_type.code or '').strip().upper()
        return self.FORM_CODE_BELT

    @property
    def is_level_form(self):
        return self.form_code.startswith(self.FORM_CODE_LEVEL)

    @property
    def is_scanner_form(self):
        code = self.form_code
        if self.FORM_CODE_SCANNER in code:
            return True
        title = ''
        if self.form_type_id and self.form_type:
            title = (self.form_type.title or '').strip().upper()
        return 'SCANNER' in code or 'SCANNER' in title

    @property
    def is_flow_certificate_form(self):
        code = self.form_code
        title = ''
        if self.form_type_id and self.form_type:
            title = (self.form_type.title or '').strip().upper()
        return (
            self.FORM_CODE_FLOW_CERT in code
            or 'FOR VAZAO' in code
            or 'VALIDACAO DE CERTIFICADO' in title
            or 'CERTIFICADO DE CALIBRACAO DE MEDIDOR DE VAZAO' in title
        )

    @property
    def is_flow_adjust_form(self):
        code = self.form_code
        title = ''
        if self.form_type_id and self.form_type:
            title = (self.form_type.title or '').strip().upper()
        title_has_vazao_adjust = (
            'VAZAO' in title
            and 'VERIFICACAO' in title
            and 'AJUSTE' in title
            and 'MEDIDOR' in title
        )
        return (
            code.startswith(self.FORM_CODE_FLOW_ADJUST)
            or code.startswith(self.FORM_CODE_FLOW_ADJUST_ALT)
            or 'FOR 08.05.006' in code
            or 'FOR 08.03.006' in code
            or title_has_vazao_adjust
        )

    @property
    def is_density_form(self):
        code = self.form_code
        title = ''
        if self.form_type_id and self.form_type:
            title = (self.form_type.title or '').strip().upper()
        title_has_density_adjust = (
            'DENSIDADE' in title
            and 'TRANSMISSOR' in title
            and 'VERIFICACAO' in title
            and 'AJUSTE' in title
        )
        return (
            code.startswith(self.FORM_CODE_DENSITY)
            or 'FOR 08.03.003' in code
            or title_has_density_adjust
        )

    @property
    def is_flow_form(self):
        return self.is_flow_certificate_form

    @property
    def is_belt_form(self):
        return (
            not self.is_level_form
            and not self.is_scanner_form
            and not self.is_flow_certificate_form
            and not self.is_flow_adjust_form
            and not self.is_density_form
        )

    @staticmethod
    def _avg(*values):
        valid = [v for v in values if v is not None]
        if not valid:
            return None
        return sum(valid) / Decimal(len(valid))

    @staticmethod
    def _to_decimal(value):
        if value is None:
            return None
        if isinstance(value, Decimal):
            return value
        try:
            return Decimal(str(value))
        except Exception:
            return None

    @classmethod
    def _sqrt(cls, value):
        decimal_value = cls._to_decimal(value)
        if decimal_value is None or decimal_value < 0:
            return None
        with localcontext() as context:
            context.prec = 28
            return decimal_value.sqrt()

    @staticmethod
    def _std_sample(values):
        valid = [v for v in values if v is not None]
        count = len(valid)
        if count < 2:
            return None
        mean = sum(valid) / Decimal(count)
        sum_square = sum((value - mean) * (value - mean) for value in valid)
        variance = sum_square / Decimal(count - 1)
        return FormSubmission._sqrt(variance)

    @property
    def scanner_points(self):
        rows = []
        for index in range(1, 7):
            target = (getattr(self, f'scanner_target_{index}', '') or '').strip() or f'Refletor {index}'
            nominal = getattr(self, f'scanner_nominal_{index}_m', None)
            measured = getattr(self, f'scanner_measured_{index}_m', None)
            error_m = None
            error_abs_mm = None
            ca_manufacturer_mm = None
            ok_fixed = None
            ok_manufacturer = None
            if nominal is not None and measured is not None:
                error_m = measured - nominal
                error_abs_mm = abs(error_m) * Decimal('1000')
                ca_manufacturer_mm = self.scanner_ca_manufacturer_for_distance(nominal)
                if self.acceptance_limit_pct is not None:
                    ok_fixed = (error_abs_mm + (self.scanner_u_expanded_mm or Decimal('0'))) <= self.acceptance_limit_pct
                if ca_manufacturer_mm is not None:
                    ok_manufacturer = (error_abs_mm + (self.scanner_u_expanded_mm or Decimal('0'))) <= ca_manufacturer_mm
            rows.append(
                {
                    'index': index,
                    'target': target,
                    'nominal_m': nominal,
                    'measured_m': measured,
                    'error_m': error_m,
                    'error_abs_mm': error_abs_mm,
                    'ca_fixed_mm': self.acceptance_limit_pct,
                    'ca_manufacturer_mm': ca_manufacturer_mm,
                    'ok_fixed': ok_fixed,
                    'ok_manufacturer': ok_manufacturer,
                }
            )
        return rows

    @property
    def scanner_valid_points(self):
        return [row for row in self.scanner_points if row['nominal_m'] is not None and row['measured_m'] is not None]

    @property
    def scanner_error_abs_values_mm(self):
        return [row['error_abs_mm'] for row in self.scanner_valid_points if row['error_abs_mm'] is not None]

    @property
    def scanner_max_error_abs_mm(self):
        values = self.scanner_error_abs_values_mm
        return max(values) if values else None

    @property
    def scanner_k_factor_value(self):
        return self.scanner_k_factor if self.scanner_k_factor is not None else Decimal('2')

    @property
    def scanner_manufacturer_ppm_value(self):
        return self.scanner_manufacturer_ppm if self.scanner_manufacturer_ppm is not None else Decimal('10')

    @property
    def scanner_u_components_mm(self):
        return [
            self.scanner_u_ref_mm or Decimal('0'),
            self.scanner_u_rep_mm or Decimal('0'),
            self.scanner_u_res_mm or Decimal('0'),
            self.scanner_u_setup_mm or Decimal('0'),
            self.scanner_u_env_mm or Decimal('0'),
        ]

    @property
    def scanner_u_combined_mm(self):
        components = self.scanner_u_components_mm
        if not components:
            return None
        sum_squares = sum(component * component for component in components)
        return self._sqrt(sum_squares)

    @property
    def scanner_u_expanded_mm(self):
        u_combined = self.scanner_u_combined_mm
        if u_combined is None:
            return None
        return abs(self.scanner_k_factor_value * u_combined)

    def scanner_ca_manufacturer_for_distance(self, distance_m):
        if distance_m is None or self.acceptance_limit_pct is None:
            return None
        ppm = self.scanner_manufacturer_ppm_value
        return self.acceptance_limit_pct + (ppm * Decimal('0.001') * distance_m)

    @property
    def scanner_status_fixed(self):
        valid_rows = self.scanner_valid_points
        if not valid_rows:
            return 'Pendente dados'
        if self.acceptance_limit_pct is None or self.scanner_u_expanded_mm is None:
            return 'Pendente dados'
        if all(row['ok_fixed'] for row in valid_rows):
            return 'Aprovado'
        return 'Reprovado'

    @property
    def scanner_status_manufacturer(self):
        valid_rows = self.scanner_valid_points
        if not valid_rows:
            return 'Pendente dados'
        if self.scanner_u_expanded_mm is None:
            return 'Pendente dados'
        if all(row['ok_manufacturer'] for row in valid_rows):
            return 'Aprovado'
        return 'Reprovado'

    @property
    def flow_points(self):
        rows = []
        for index in range(1, 7):
            target = (getattr(self, f'flow_point_label_{index}', '') or '').strip() or f'Ponto {index}'
            calibration = getattr(self, f'flow_calibration_{index}_m3h', None)
            indicated = getattr(self, f'flow_indicated_{index}_m3h', None)
            reference = getattr(self, f'flow_reference_{index}_m3h', None)
            tendency = getattr(self, f'flow_tendency_{index}_pct', None)
            uncertainty = getattr(self, f'flow_uncertainty_{index}_pct', None)
            k_value = getattr(self, f'flow_k_{index}', None)

            if tendency is None and indicated is not None and reference not in (None, 0):
                try:
                    tendency = ((indicated / reference) - Decimal('1')) * Decimal('100')
                except Exception:
                    tendency = None

            error_abs = abs(tendency) if tendency is not None else None
            combined = None
            ok = None
            if error_abs is not None and uncertainty is not None:
                combined = error_abs + abs(uncertainty)
                if self.acceptance_limit_pct is not None:
                    ok = combined <= self.acceptance_limit_pct

            rows.append(
                {
                    'index': index,
                    'target': target,
                    'calibration_m3h': calibration,
                    'indicated_m3h': indicated,
                    'reference_m3h': reference,
                    'tendency_pct': tendency,
                    'error_abs_pct': error_abs,
                    'uncertainty_pct': uncertainty,
                    'k_value': k_value,
                    'combined_pct': combined,
                    'ok': ok,
                }
            )
        return rows

    @property
    def flow_valid_points(self):
        return [
            row
            for row in self.flow_points
            if row['tendency_pct'] is not None and row['uncertainty_pct'] is not None
        ]

    @property
    def flow_error_abs_values_pct(self):
        return [row['error_abs_pct'] for row in self.flow_valid_points if row['error_abs_pct'] is not None]

    @property
    def flow_uncertainty_values_pct(self):
        return [abs(row['uncertainty_pct']) for row in self.flow_valid_points if row['uncertainty_pct'] is not None]

    @property
    def flow_max_error_abs_pct(self):
        values = self.flow_error_abs_values_pct
        return max(values) if values else None

    @property
    def flow_max_uncertainty_pct(self):
        values = self.flow_uncertainty_values_pct
        return max(values) if values else None

    @property
    def flow_combined_values_pct(self):
        return [
            row['combined_pct']
            for row in self.flow_valid_points
            if row['combined_pct'] is not None
        ]

    @property
    def flow_max_combined_pct(self):
        values = self.flow_combined_values_pct
        return max(values) if values else None

    @property
    def flow_valid_points_count(self):
        return len(self.flow_valid_points)

    @property
    def flow_approved_points_count(self):
        return sum(1 for row in self.flow_valid_points if row.get('ok') is True)

    @property
    def flow_status(self):
        valid_rows = self.flow_valid_points
        if self.acceptance_limit_pct is None:
            return 'Pendente dados'
        if not valid_rows:
            return 'Pendente dados'
        if any(row['ok'] is False for row in valid_rows):
            return 'Reprovado'
        if all(row['ok'] is True for row in valid_rows):
            return 'Aprovado'
        return 'Pendente dados'

    @staticmethod
    def _duration_minutes(start_time, end_time):
        if not start_time or not end_time:
            return None
        start_dt = datetime.combine(timezone.localdate(), start_time)
        end_dt = datetime.combine(timezone.localdate(), end_time)
        if end_dt < start_dt:
            end_dt += timedelta(days=1)
        delta = end_dt - start_dt
        minutes = Decimal(str(delta.total_seconds())) / Decimal('60')
        if minutes <= 0:
            return None
        return minutes

    @property
    def flow_adjust_pipe_thickness_values_mm(self):
        return [
            value
            for value in [
                self.flow_adjust_thickness_1_mm,
                self.flow_adjust_thickness_2_mm,
                self.flow_adjust_thickness_3_mm,
                self.flow_adjust_thickness_4_mm,
            ]
            if value is not None
        ]

    @property
    def flow_adjust_pipe_thickness_mean_mm(self):
        return self._avg(*self.flow_adjust_pipe_thickness_values_mm)

    @property
    def flow_adjust_pipe_thickness_std_mm(self):
        return self._std_sample(self.flow_adjust_pipe_thickness_values_mm)

    @property
    def flow_adjust_external_diameter_mm(self):
        circumference = self.flow_adjust_circumference_ci_mm
        if circumference is not None and circumference > 0:
            return circumference / Decimal('3.14')
        if self.flow_adjust_pipe_nominal_in is not None:
            return self.flow_adjust_pipe_nominal_in * Decimal('25.4')
        return None

    @property
    def flow_adjust_external_diameter_source_label(self):
        circumference = self.flow_adjust_circumference_ci_mm
        if circumference is not None and circumference > 0:
            return 'DE = CI / PI'
        if self.flow_adjust_pipe_nominal_in is not None:
            return "DE = POL' x 25,4"
        return '-'

    @property
    def flow_adjust_u_ci_mm_value(self):
        if self.flow_adjust_u_ci_mm is not None:
            return self.flow_adjust_u_ci_mm
        return Decimal('1.000')

    @property
    def flow_adjust_u_inst_t_mm_value(self):
        if self.flow_adjust_u_inst_t_mm is not None:
            return self.flow_adjust_u_inst_t_mm
        return Decimal('0.200')

    @property
    def flow_adjust_u_delta_t_s_value(self):
        if self.flow_adjust_u_delta_t_s is not None:
            return self.flow_adjust_u_delta_t_s
        return Decimal('5.000')

    @property
    def flow_adjust_u_dut_repeat_pct_value(self):
        if self.flow_adjust_u_dut_repeat_pct is not None:
            return self.flow_adjust_u_dut_repeat_pct
        return Decimal('0')

    @property
    def flow_adjust_u_dut_res_pct_value(self):
        if self.flow_adjust_u_dut_res_pct is not None:
            return self.flow_adjust_u_dut_res_pct
        return Decimal('0')

    @property
    def flow_adjust_k_factor_value(self):
        if self.flow_adjust_k_factor is not None:
            return self.flow_adjust_k_factor
        return Decimal('2')

    @property
    def flow_adjust_u_de_mm(self):
        circumference = self.flow_adjust_circumference_ci_mm
        if circumference is not None and circumference > 0:
            return self.flow_adjust_u_ci_mm_value / Decimal('3.14')
        if self.flow_adjust_pipe_nominal_in is not None:
            return Decimal('0')
        return None

    @property
    def flow_adjust_u_t_bar_mm(self):
        values = self.flow_adjust_pipe_thickness_values_mm
        count = len(values)
        if count == 0:
            return None
        std_sample = self._std_sample(values)
        std_term = Decimal('0')
        if std_sample is not None:
            sqrt_count = self._sqrt(Decimal(count))
            if sqrt_count in (None, 0):
                return None
            std_term = std_sample / sqrt_count
        inst = self.flow_adjust_u_inst_t_mm_value
        return self._sqrt((std_term * std_term) + (inst * inst))

    @property
    def flow_adjust_internal_diameter_mm(self):
        de = self.flow_adjust_external_diameter_mm
        t_mean = self.flow_adjust_pipe_thickness_mean_mm
        if de is None or t_mean is None:
            return None
        return de - (Decimal('2') * t_mean)

    @property
    def flow_adjust_u_internal_diameter_mm(self):
        u_de = self.flow_adjust_u_de_mm
        u_t_bar = self.flow_adjust_u_t_bar_mm
        if u_de is None or u_t_bar is None:
            return None
        return self._sqrt((u_de * u_de) + ((Decimal('2') * u_t_bar) ** 2))

    @property
    def flow_adjust_u_rel_geom_pct(self):
        di = self.flow_adjust_internal_diameter_mm
        u_di = self.flow_adjust_u_internal_diameter_mm
        if di in (None, 0) or u_di is None:
            return None
        return Decimal('100') * (Decimal('2') * u_di / di)

    @property
    def flow_adjust_before_duration_min(self):
        return self._duration_minutes(
            self.flow_adjust_before_totmv_start_time,
            self.flow_adjust_before_totmv_end_time,
        )

    @property
    def flow_adjust_after_duration_min(self):
        return self._duration_minutes(
            self.flow_adjust_after_totmv_start_time,
            self.flow_adjust_after_totmv_end_time,
        )

    @property
    def flow_adjust_before_ratio_r(self):
        if self.flow_adjust_before_totmv_m3 in (None, 0) or self.flow_adjust_before_totsup_m3 is None:
            return None
        return self.flow_adjust_before_totsup_m3 / self.flow_adjust_before_totmv_m3

    @property
    def flow_adjust_after_ratio_r(self):
        if self.flow_adjust_after_totmv_m3 in (None, 0) or self.flow_adjust_after_totsup_m3 is None:
            return None
        return self.flow_adjust_after_totsup_m3 / self.flow_adjust_after_totmv_m3

    @property
    def flow_adjust_error_before_pct_auto(self):
        ratio = self.flow_adjust_before_ratio_r
        if ratio is None:
            return None
        return (ratio - Decimal('1')) * Decimal('100')

    @property
    def flow_adjust_error_after_pct_auto(self):
        ratio = self.flow_adjust_after_ratio_r
        if ratio is None:
            return None
        return (ratio - Decimal('1')) * Decimal('100')

    @property
    def flow_adjust_has_after_data(self):
        return self.flow_adjust_after_totmv_m3 is not None and self.flow_adjust_after_totsup_m3 is not None

    @property
    def flow_adjust_final_phase(self):
        return 'after' if self.flow_adjust_has_after_data else 'before'

    @property
    def flow_adjust_final_phase_label(self):
        return 'Apos ajuste' if self.flow_adjust_has_after_data else 'Antes do ajuste'

    @property
    def flow_adjust_final_ratio_r(self):
        if self.flow_adjust_has_after_data:
            return self.flow_adjust_after_ratio_r
        return self.flow_adjust_before_ratio_r

    @property
    def flow_adjust_final_error_pct(self):
        if self.flow_adjust_has_after_data:
            return self.flow_adjust_error_after_pct_auto
        return self.flow_adjust_error_before_pct_auto

    @property
    def flow_adjust_final_duration_min(self):
        if self.flow_adjust_has_after_data and self.flow_adjust_after_duration_min is not None:
            return self.flow_adjust_after_duration_min
        return self.flow_adjust_before_duration_min

    @property
    def flow_adjust_u_rel_tempo_pct(self):
        duration_min = self.flow_adjust_final_duration_min
        if duration_min in (None, 0):
            return None
        return Decimal('100') * self.flow_adjust_u_delta_t_s_value / (duration_min * Decimal('60'))

    @property
    def flow_adjust_u_ref_total_pct(self):
        u_rel_geom = self.flow_adjust_u_rel_geom_pct
        u_rel_tempo = self.flow_adjust_u_rel_tempo_pct
        if u_rel_geom is None or u_rel_tempo is None:
            return None
        u_cal_maleta = Decimal('0')
        u_repeat_maleta = Decimal('0')
        u_alg = Decimal('0')
        return self._sqrt(
            (u_cal_maleta * u_cal_maleta)
            + (u_repeat_maleta * u_repeat_maleta)
            + (u_alg * u_alg)
            + (u_rel_geom * u_rel_geom)
            + (u_rel_tempo * u_rel_tempo)
        )

    @property
    def flow_adjust_u_dut_total_pct(self):
        repeat = self.flow_adjust_u_dut_repeat_pct_value
        resolution = self.flow_adjust_u_dut_res_pct_value
        return self._sqrt((repeat * repeat) + (resolution * resolution))

    @property
    def flow_adjust_u_rel_r_pct(self):
        u_ref_total = self.flow_adjust_u_ref_total_pct
        u_dut_total = self.flow_adjust_u_dut_total_pct
        if u_ref_total is None or u_dut_total is None:
            return None
        return self._sqrt((u_ref_total * u_ref_total) + (u_dut_total * u_dut_total))

    @property
    def flow_adjust_u_error_pct(self):
        ratio = self.flow_adjust_final_ratio_r
        u_rel_r = self.flow_adjust_u_rel_r_pct
        if ratio is None or u_rel_r is None:
            return None
        return abs(ratio * u_rel_r)

    @property
    def flow_adjust_u_expanded_pct(self):
        u_error = self.flow_adjust_u_error_pct
        if u_error is None:
            return None
        return abs(self.flow_adjust_k_factor_value * u_error)

    @property
    def flow_adjust_error_before_ok(self):
        before_error = self.flow_adjust_error_before_pct_auto
        if before_error is None or self.acceptance_limit_pct is None:
            return None
        return abs(before_error) <= self.acceptance_limit_pct

    @property
    def flow_adjust_error_final_ok(self):
        final_error = self.flow_adjust_final_error_pct
        if final_error is None or self.acceptance_limit_pct is None:
            return None
        return abs(final_error) <= self.acceptance_limit_pct

    @property
    def density_scale_criterion_value(self):
        if self.density_scale_criterion_pct is not None:
            return self.density_scale_criterion_pct
        return Decimal('1.000')

    @property
    def density_scale_u_additional_kg_value(self):
        if self.density_scale_u_additional_kg is not None:
            return self.density_scale_u_additional_kg
        return Decimal('0.0000')

    @property
    def density_volume_graduation_l_value(self):
        if self.density_volume_graduation_l is not None:
            return self.density_volume_graduation_l
        for index in range(1, 4):
            standard = getattr(self, f'density_standard_{index}', None)
            if standard and standard.graduation_l is not None:
                return standard.graduation_l
        return Decimal('0.0100')

    @property
    def density_mds_resolution_gcm3_value(self):
        if self.density_mds_resolution_gcm3 is not None:
            return self.density_mds_resolution_gcm3
        return Decimal('0.0010')

    @property
    def density_k_factor_value(self):
        if self.density_k_factor is not None:
            return self.density_k_factor
        return Decimal('2.000')

    @property
    def density_u_volume_l(self):
        graduation = self.density_volume_graduation_l_value
        sqrt_twelve = self._sqrt(Decimal('12'))
        if graduation is None or sqrt_twelve in (None, 0):
            return None
        return graduation / sqrt_twelve

    @property
    def density_u_res_mds_gcm3(self):
        resolution = self.density_mds_resolution_gcm3_value
        sqrt_twelve = self._sqrt(Decimal('12'))
        if resolution is None or sqrt_twelve in (None, 0):
            return None
        return resolution / sqrt_twelve

    @property
    def density_scale_error_pct(self):
        mab = self.density_scale_mab_kg
        mib = self.density_scale_mib_kg
        if mab in (None, 0) or mib is None:
            return None
        return ((mib / mab) - Decimal('1')) * Decimal('100')

    @property
    def density_scale_ok(self):
        criterion = self.density_scale_criterion_value
        error = self.density_scale_error_pct
        if criterion is None or error is None:
            return None
        return abs(error) <= criterion

    @property
    def density_scale_status_label(self):
        ok = self.density_scale_ok
        if ok is None:
            return 'Pendente dados'
        return 'OK' if ok else 'NOK'

    def _density_phase_has_data(self, phase='before'):
        prefix = f'density_{phase}_'
        for index in range(1, 4):
            if getattr(self, f'{prefix}empty_{index}_kg', None) is not None:
                return True
            if getattr(self, f'{prefix}full_{index}_kg', None) is not None:
                return True
            if getattr(self, f'{prefix}volume_{index}_l', None) is not None:
                return True
        if getattr(self, f'{prefix}mds_informed_gcm3', None) is not None:
            return True
        for index in range(1, 6):
            if getattr(self, f'{prefix}mds_reading_{index}_gcm3', None) is not None:
                return True
        if getattr(self, f'{prefix}low_point_gcm3', None) is not None:
            return True
        if getattr(self, f'{prefix}high_point_gcm3', None) is not None:
            return True
        if getattr(self, f'{prefix}low_count_cts', None) is not None:
            return True
        if getattr(self, f'{prefix}high_count_cts', None) is not None:
            return True
        return False

    def _density_phase_rows(self, phase='before'):
        rows = []
        phase_prefix = f'density_{phase}_'
        scale_error_pct = self.density_scale_error_pct
        u_additional = self.density_scale_u_additional_kg_value or Decimal('0')
        u_volume = self.density_u_volume_l
        sqrt_three = self._sqrt(Decimal('3'))

        for index in range(1, 4):
            standard = getattr(self, f'density_standard_{index}', None)
            label = standard.tag if standard else f'Aferidor {index}'
            empty = getattr(self, f'{phase_prefix}empty_{index}_kg', None)
            full = getattr(self, f'{phase_prefix}full_{index}_kg', None)
            volume = getattr(self, f'{phase_prefix}volume_{index}_l', None)

            mass = None
            density = None
            u_empty = None
            u_full = None
            u_mass = None
            u_density = None

            if empty is not None and full is not None:
                mass = full - empty
            if mass is not None and volume not in (None, 0):
                density = mass / volume

            if empty is not None:
                balance_term = Decimal('0')
                if scale_error_pct is not None and sqrt_three not in (None, 0):
                    balance_term = (abs(scale_error_pct) / Decimal('100')) * empty / sqrt_three
                u_empty = self._sqrt((balance_term * balance_term) + (u_additional * u_additional))
            if full is not None:
                balance_term = Decimal('0')
                if scale_error_pct is not None and sqrt_three not in (None, 0):
                    balance_term = (abs(scale_error_pct) / Decimal('100')) * full / sqrt_three
                u_full = self._sqrt((balance_term * balance_term) + (u_additional * u_additional))
            if u_empty is not None and u_full is not None:
                u_mass = self._sqrt((u_empty * u_empty) + (u_full * u_full))

            if (
                density is not None
                and mass not in (None, 0)
                and volume not in (None, 0)
                and u_mass is not None
                and u_volume is not None
            ):
                term_mass = u_mass / mass
                term_volume = u_volume / volume
                u_density = density * self._sqrt((term_mass * term_mass) + (term_volume * term_volume))

            rows.append(
                {
                    'index': index,
                    'label': label,
                    'standard': standard,
                    'empty_kg': empty,
                    'full_kg': full,
                    'mass_kg': mass,
                    'volume_l': volume,
                    'density_gcm3': density,
                    'u_empty_kg': u_empty,
                    'u_full_kg': u_full,
                    'u_mass_kg': u_mass,
                    'u_volume_l': u_volume,
                    'u_density_gcm3': u_density,
                }
            )
        return rows

    def _density_phase_mds_readings(self, phase='before'):
        prefix = f'density_{phase}_mds_reading_'
        values = []
        for index in range(1, 6):
            value = getattr(self, f'{prefix}{index}_gcm3', None)
            if value is not None:
                values.append(value)
        return values

    def _density_phase_mds_mean(self, phase='before'):
        readings = self._density_phase_mds_readings(phase)
        if readings:
            return self._avg(*readings)
        return getattr(self, f'density_{phase}_mds_informed_gcm3', None)

    def _density_phase_metrics(self, phase='before'):
        rows = self._density_phase_rows(phase)
        valid_rows = [row for row in rows if row['density_gcm3'] is not None]
        densities = [row['density_gcm3'] for row in valid_rows]
        mda = self._avg(*densities) if densities else None

        s_density = self._std_sample(densities) if len(densities) > 1 else Decimal('0') if densities else None
        u_a_mda = None
        if densities:
            if len(densities) <= 1:
                u_a_mda = Decimal('0')
            else:
                sqrt_n = self._sqrt(Decimal(len(densities)))
                if s_density is not None and sqrt_n not in (None, 0):
                    u_a_mda = s_density / sqrt_n

        u_b_mda = None
        if valid_rows:
            u_density_values = [row['u_density_gcm3'] or Decimal('0') for row in valid_rows]
            sum_sq = sum(value * value for value in u_density_values)
            u_b_mda = self._sqrt(sum_sq) / Decimal(len(valid_rows))

        u_mda = None
        if u_a_mda is not None and u_b_mda is not None:
            u_mda = self._sqrt((u_a_mda * u_a_mda) + (u_b_mda * u_b_mda))

        mds_readings = self._density_phase_mds_readings(phase)
        mds = self._density_phase_mds_mean(phase)
        u_a_mds = None
        if mds is not None:
            if len(mds_readings) <= 1:
                u_a_mds = Decimal('0')
            else:
                std_mds = self._std_sample(mds_readings)
                sqrt_n = self._sqrt(Decimal(len(mds_readings)))
                if std_mds is not None and sqrt_n not in (None, 0):
                    u_a_mds = std_mds / sqrt_n

        u_res_mds = self.density_u_res_mds_gcm3
        u_mds = None
        if u_a_mds is not None and u_res_mds is not None:
            u_mds = self._sqrt((u_a_mds * u_a_mds) + (u_res_mds * u_res_mds))

        error_pct = None
        if mda not in (None, 0) and mds is not None:
            error_pct = ((mds / mda) - Decimal('1')) * Decimal('100')

        u_error_pct = None
        if mda not in (None, 0) and mds not in (None, 0) and u_mda is not None and u_mds is not None:
            ratio_abs = abs(mds / mda) * Decimal('100')
            term_mds = u_mds / mds
            term_mda = u_mda / mda
            u_error_pct = ratio_abs * self._sqrt((term_mds * term_mds) + (term_mda * term_mda))

        u_expanded_pct = None
        if u_error_pct is not None:
            u_expanded_pct = abs(self.density_k_factor_value * u_error_pct)

        margin_pct = None
        if error_pct is not None and u_expanded_pct is not None:
            margin_pct = abs(error_pct) + u_expanded_pct

        return {
            'rows': rows,
            'valid_rows': valid_rows,
            'mda_gcm3': mda,
            's_density_gcm3': s_density,
            'u_a_mda_gcm3': u_a_mda,
            'u_b_mda_gcm3': u_b_mda,
            'u_mda_gcm3': u_mda,
            'mds_gcm3': mds,
            'mds_readings': mds_readings,
            'u_a_mds_gcm3': u_a_mds,
            'u_res_mds_gcm3': u_res_mds,
            'u_mds_gcm3': u_mds,
            'error_pct': error_pct,
            'u_error_pct': u_error_pct,
            'u_expanded_pct': u_expanded_pct,
            'margin_pct': margin_pct,
        }

    @property
    def density_before_rows(self):
        return self._density_phase_metrics('before')['rows']

    @property
    def density_after_rows(self):
        return self._density_phase_metrics('after')['rows']

    @property
    def density_before_mda_gcm3(self):
        return self._density_phase_metrics('before')['mda_gcm3']

    @property
    def density_after_mda_gcm3(self):
        return self._density_phase_metrics('after')['mda_gcm3']

    @property
    def density_before_mds_gcm3(self):
        return self._density_phase_metrics('before')['mds_gcm3']

    @property
    def density_after_mds_gcm3(self):
        return self._density_phase_metrics('after')['mds_gcm3']

    @property
    def density_before_error_pct(self):
        return self._density_phase_metrics('before')['error_pct']

    @property
    def density_after_error_pct(self):
        return self._density_phase_metrics('after')['error_pct']

    @property
    def density_before_u_expanded_pct(self):
        return self._density_phase_metrics('before')['u_expanded_pct']

    @property
    def density_after_u_expanded_pct(self):
        return self._density_phase_metrics('after')['u_expanded_pct']

    @property
    def density_before_margin_pct(self):
        return self._density_phase_metrics('before')['margin_pct']

    @property
    def density_after_margin_pct(self):
        return self._density_phase_metrics('after')['margin_pct']

    @property
    def density_before_u_mda_gcm3(self):
        return self._density_phase_metrics('before')['u_mda_gcm3']

    @property
    def density_before_u_mds_gcm3(self):
        return self._density_phase_metrics('before')['u_mds_gcm3']

    @property
    def density_after_u_mda_gcm3(self):
        return self._density_phase_metrics('after')['u_mda_gcm3']

    @property
    def density_after_u_mds_gcm3(self):
        return self._density_phase_metrics('after')['u_mds_gcm3']

    @property
    def density_before_u_error_pct(self):
        return self._density_phase_metrics('before')['u_error_pct']

    @property
    def density_after_u_error_pct(self):
        return self._density_phase_metrics('after')['u_error_pct']

    @property
    def density_has_after_data(self):
        return self._density_phase_has_data('after')

    @property
    def density_after_is_evaluable(self):
        metrics = self._density_phase_metrics('after')
        return (
            metrics['error_pct'] is not None
            and metrics['u_expanded_pct'] is not None
            and metrics['margin_pct'] is not None
        )

    @property
    def density_final_phase(self):
        return 'after' if self.density_after_is_evaluable else 'before'

    @property
    def density_final_phase_label(self):
        return 'Após ajuste' if self.density_final_phase == 'after' else 'Antes do ajuste'

    @property
    def density_final_error_pct(self):
        return self.density_after_error_pct if self.density_final_phase == 'after' else self.density_before_error_pct

    @property
    def density_final_u_expanded_pct(self):
        return (
            self.density_after_u_expanded_pct
            if self.density_final_phase == 'after'
            else self.density_before_u_expanded_pct
        )

    @property
    def density_final_margin_pct(self):
        return self.density_after_margin_pct if self.density_final_phase == 'after' else self.density_before_margin_pct

    @property
    def density_final_u_mda_gcm3(self):
        return self.density_after_u_mda_gcm3 if self.density_final_phase == 'after' else self.density_before_u_mda_gcm3

    @property
    def density_final_u_mds_gcm3(self):
        return self.density_after_u_mds_gcm3 if self.density_final_phase == 'after' else self.density_before_u_mds_gcm3

    @property
    def density_final_u_error_pct(self):
        return self.density_after_u_error_pct if self.density_final_phase == 'after' else self.density_before_u_error_pct

    @property
    def density_before_status_label(self):
        if self.density_scale_ok is False:
            return 'Inválido - balança NOK'
        if self.density_before_margin_pct is None or self.acceptance_limit_pct is None:
            return 'Pendente dados'
        return 'Aprovado' if self.density_before_margin_pct <= self.acceptance_limit_pct else 'Reprovado'

    @property
    def density_after_status_label(self):
        if self.density_scale_ok is False:
            return 'Inválido - balança NOK'
        if self.density_after_margin_pct is None or self.acceptance_limit_pct is None:
            return 'Pendente dados'
        return 'Aprovado' if self.density_after_margin_pct <= self.acceptance_limit_pct else 'Reprovado'

    @property
    def density_final_status_label(self):
        if self.density_scale_ok is False:
            return 'Inválido - balança NOK'
        if self.density_final_margin_pct is None or self.acceptance_limit_pct is None:
            return 'Pendente dados'
        return 'Aprovado' if self.density_final_margin_pct <= self.acceptance_limit_pct else 'Reprovado'

    @property
    def attached_certificate_file(self):
        if self.is_scanner_form:
            return self.scanner_certificate_file
        if self.is_flow_form:
            return self.flow_certificate_file
        return None

    def _level_points(self, phase='before'):
        if phase == 'after':
            return [
                (self.level_after_vm_1, self.level_after_vl_1),
                (self.level_after_vm_2, self.level_after_vl_2),
                (self.level_after_vm_3, self.level_after_vl_3),
                (self.level_after_vm_4, self.level_after_vl_4),
            ]
        return [
            (self.level_before_vm_1, self.level_before_vl_1),
            (self.level_before_vm_2, self.level_before_vl_2),
            (self.level_before_vm_3, self.level_before_vl_3),
            (self.level_before_vm_4, self.level_before_vl_4),
        ]

    def _level_rows(self, phase='before'):
        rows = []
        for index, (vm_value, vl_value) in enumerate(self._level_points(phase), start=1):
            error_signed = None
            error_abs_m = None
            if vm_value is not None and vl_value is not None:
                error_signed = vl_value - vm_value
                error_abs_m = abs(error_signed)
            rows.append(
                {
                    'index': index,
                    'vm': vm_value,
                    'vl': vl_value,
                    'error_signed_m': error_signed,
                    'error_abs_m': error_abs_m,
                }
            )
        return rows

    def _level_signed_errors_m(self, phase='before'):
        values = []
        for vm_value, vl_value in self._level_points(phase):
            if vm_value is None or vl_value is None:
                continue
            values.append(vl_value - vm_value)
        return values

    @property
    def level_has_after_measurements(self):
        return bool(self._level_signed_errors_m('after'))

    @property
    def level_final_phase(self):
        return 'after' if self.level_has_after_measurements else 'before'

    @property
    def level_final_phase_label(self):
        return 'Após ajuste' if self.level_has_after_measurements else 'Antes do ajuste'

    @property
    def level_before_rows(self):
        return self._level_rows('before')

    @property
    def level_after_rows(self):
        return self._level_rows('after')

    def _level_mean_abs_m(self, phase='before'):
        errors = self._level_signed_errors_m(phase)
        if not errors:
            return None
        abs_values = [abs(v) for v in errors]
        return self._avg(*abs_values)

    def _level_mean_signed_m(self, phase='before'):
        errors = self._level_signed_errors_m(phase)
        if not errors:
            return None
        return self._avg(*errors)

    @property
    def level_before_mean_abs_m(self):
        return self._level_mean_abs_m('before')

    @property
    def level_after_mean_abs_m(self):
        return self._level_mean_abs_m('after')

    @property
    def level_final_mean_abs_m(self):
        return self._level_mean_abs_m(self.level_final_phase)

    @property
    def level_before_mean_abs_cm(self):
        return self.level_before_mean_abs_m

    @property
    def level_after_mean_abs_cm(self):
        return self.level_after_mean_abs_m

    @property
    def level_final_mean_abs_cm(self):
        return self.level_final_mean_abs_m

    @property
    def level_final_mean_signed_m(self):
        return self._level_mean_signed_m(self.level_final_phase)

    def _level_repeatability_u_a_for_phase_m(self, phase='before'):
        errors = self._level_signed_errors_m(phase)
        count = len(errors)
        if count < 2:
            return None
        std_sample = self._std_sample(errors)
        sqrt_count = self._sqrt(Decimal(count))
        if std_sample is None or sqrt_count in (None, 0):
            return None
        return std_sample / sqrt_count

    @property
    def level_repeatability_u_a_m(self):
        return self._level_repeatability_u_a_for_phase_m(self.level_final_phase)

    @property
    def level_resolution_tape_value_m(self):
        if self.level_resolution_tape_m is not None:
            return self.level_resolution_tape_m
        return Decimal('0.001')

    @property
    def level_resolution_instrument_value_m(self):
        if self.level_resolution_instrument_m is not None:
            return self.level_resolution_instrument_m
        return Decimal('0.010')

    @property
    def level_coverage_factor_value(self):
        if self.level_coverage_factor_k is not None:
            return self.level_coverage_factor_k
        return Decimal('2')

    @property
    def level_resolution_u_vm_m(self):
        sqrt_twelve = self._sqrt(Decimal('12'))
        if sqrt_twelve in (None, 0):
            return None
        return self.level_resolution_tape_value_m / sqrt_twelve

    @property
    def level_resolution_u_vl_m(self):
        sqrt_twelve = self._sqrt(Decimal('12'))
        if sqrt_twelve in (None, 0):
            return None
        return self.level_resolution_instrument_value_m / sqrt_twelve

    @property
    def level_resolution_u_b_m(self):
        u_vm = self.level_resolution_u_vm_m
        u_vl = self.level_resolution_u_vl_m
        if u_vm is None or u_vl is None:
            return None
        return self._sqrt((u_vm * u_vm) + (u_vl * u_vl))

    def _level_uncertainty_u_c_for_phase_m(self, phase='before'):
        u_a = self._level_repeatability_u_a_for_phase_m(phase)
        u_b = self.level_resolution_u_b_m
        if u_a is None or u_b is None:
            return None
        return self._sqrt((u_a * u_a) + (u_b * u_b))

    @property
    def level_uncertainty_u_c_m(self):
        return self._level_uncertainty_u_c_for_phase_m(self.level_final_phase)

    def _level_uncertainty_expanded_for_phase_m(self, phase='before'):
        u_c = self._level_uncertainty_u_c_for_phase_m(phase)
        if u_c is None:
            return None
        return abs(self.level_coverage_factor_value * u_c)

    @property
    def level_uncertainty_expanded_before_m(self):
        return self._level_uncertainty_expanded_for_phase_m('before')

    @property
    def level_uncertainty_expanded_after_m(self):
        if not self.level_has_after_measurements:
            return None
        return self._level_uncertainty_expanded_for_phase_m('after')

    @property
    def level_uncertainty_expanded_m(self):
        return self._level_uncertainty_expanded_for_phase_m(self.level_final_phase)

    @property
    def level_uncertainty_expanded_cm(self):
        return self.level_uncertainty_expanded_m

    @property
    def level_before_within_criterion(self):
        if not self.is_level_form:
            return None
        before_error = self.level_before_mean_abs_m
        limit = self.acceptance_limit_pct
        if before_error is None or limit is None:
            return None
        return before_error <= limit

    @property
    def level_tur_value(self):
        uncertainty = self.level_uncertainty_expanded_m
        limit = self.acceptance_limit_pct
        if uncertainty in (None, 0) or limit is None:
            return None
        return limit / uncertainty

    @staticmethod
    def _is_within_limit(value, limit):
        if value is None or limit is None:
            return None
        return value <= limit

    @property
    def level_before_error_ok(self):
        return self._is_within_limit(self.level_before_mean_abs_m, self.acceptance_limit_pct)

    @property
    def level_after_error_ok(self):
        if not self.level_has_after_measurements:
            return None
        return self._is_within_limit(self.level_after_mean_abs_m, self.acceptance_limit_pct)

    @property
    def level_before_combined_value(self):
        if self.level_before_mean_abs_m is None or self.level_uncertainty_expanded_before_m is None:
            return None
        return self.level_before_mean_abs_m + abs(self.level_uncertainty_expanded_before_m)

    @property
    def level_after_combined_value(self):
        if not self.level_has_after_measurements:
            return None
        if self.level_after_mean_abs_m is None or self.level_uncertainty_expanded_after_m is None:
            return None
        return self.level_after_mean_abs_m + abs(self.level_uncertainty_expanded_after_m)

    @property
    def level_before_combined_ok(self):
        return self._is_within_limit(self.level_before_combined_value, self.acceptance_limit_pct)

    @property
    def level_after_combined_ok(self):
        return self._is_within_limit(self.level_after_combined_value, self.acceptance_limit_pct)

    @property
    def tm(self):
        return self._avg(self.t1, self.t2, self.t3)

    @property
    def md(self):
        return self._avg(self.m1, self.m2, self.m3)

    @property
    def il_before(self):
        if self.il_before_tf is None or self.il_before_ti is None:
            return None
        return (self.il_before_tf - self.il_before_ti) * Decimal('12')

    @property
    def il_after(self):
        if self.il_after_tf is None or self.il_after_ti is None:
            return None
        return (self.il_after_tf - self.il_after_ti) * Decimal('12')

    @property
    def loading_q(self):
        if self.applied_weight is None or self.bridge_length in (None, 0):
            return None
        return self.applied_weight / self.bridge_length

    @property
    def ibm_auto(self):
        return self._avg(self.pulses_per_turn_1, self.pulses_per_turn_2, self.pulses_per_turn_3)

    @property
    def belt_speed_v_auto(self):
        if self.belt_replaced:
            if self.mark_distance is None or self.md in (None, 0):
                return None
            return self.mark_distance / self.md

        if self.belt_speed_v is not None:
            return self.belt_speed_v

        if self.mark_distance is None or self.md in (None, 0):
            return None
        return self.mark_distance / self.md

    @property
    def mark_distance_auto(self):
        if self.belt_replaced:
            return self.mark_distance
        if self.belt_speed_v is None or self.md in (None, 0):
            return None
        return self.belt_speed_v * self.md

    @property
    def belt_length_auto(self):
        speed = self.belt_speed_v_auto if self.belt_speed_v_auto is not None else self.belt_speed_v
        if self.tm is None or speed is None:
            return None
        return self.tm * speed

    @property
    def speed_characteristic_b04_auto(self):
        ibm_value = self.ibm_auto if self.ibm_auto is not None else self.ibm
        length = self.belt_length_auto if self.belt_length_auto is not None else self.belt_length
        if ibm_value is None or length in (None, 0):
            return None
        return ibm_value / length

    @property
    def calculated_flow_ic_auto(self):
        speed = self.belt_speed_v_auto if self.belt_speed_v_auto is not None else self.belt_speed_v
        if self.loading_q is None or speed is None:
            return None
        return self.loading_q * speed * Decimal('3.6')

    @staticmethod
    def _error_percent(indicated, calculated):
        if indicated is None or calculated in (None, 0):
            return None
        return ((indicated / calculated) - Decimal('1')) * Decimal('100')

    @property
    def error_before_pct_auto(self):
        return self._error_percent(self.il_before, self.calculated_flow_ic_auto)

    @property
    def error_after_pct_auto(self):
        return self._error_percent(self.il_after, self.calculated_flow_ic_auto)

    @property
    def uncertainty_totalizer_resolution_r(self):
        return self.DEFAULT_UNCERTAINTY_TOTALIZER_RESOLUTION

    @property
    def uncertainty_measurement_duration_min(self):
        return self.DEFAULT_UNCERTAINTY_MEASUREMENT_DURATION_MIN

    @property
    def uncertainty_coverage_factor_k(self):
        return self.DEFAULT_UNCERTAINTY_COVERAGE_FACTOR

    @property
    def u_t_mean_auto(self):
        values = [v for v in [self.m1, self.m2, self.m3] if v is not None]
        count = len(values)
        if count < 2:
            return None
        mean = sum(values) / Decimal(count)
        sum_square = sum((value - mean) * (value - mean) for value in values)
        variance = sum_square / Decimal(count - 1)
        std_sample = self._sqrt(variance)
        sqrt_count = self._sqrt(Decimal(count))
        if std_sample is None or sqrt_count in (None, 0):
            return None
        return std_sample / sqrt_count

    @property
    def u_ic_auto(self):
        ic_value = self.calculated_flow_ic_auto
        t_mean = self.md
        u_t = self.u_t_mean_auto
        if ic_value is None or t_mean in (None, 0) or u_t is None:
            return None
        return abs(ic_value) * (u_t / t_mean)

    @property
    def u_il_auto(self):
        duration = self.uncertainty_measurement_duration_min
        resolution = self.uncertainty_totalizer_resolution_r
        sqrt_twelve = self._sqrt(Decimal('12'))
        sqrt_two = self._sqrt(Decimal('2'))
        if duration in (None, 0) or resolution is None or sqrt_twelve in (None, 0) or sqrt_two is None:
            return None
        u_t = resolution / sqrt_twelve
        u_delta_t = sqrt_two * u_t
        factor = Decimal('60') / duration
        return abs(factor * u_delta_t)

    def _expanded_uncertainty_for_il(self, il_value):
        ic_value = self.calculated_flow_ic_auto
        u_il = self.u_il_auto
        u_ic = self.u_ic_auto
        coverage_factor = self.uncertainty_coverage_factor_k
        if il_value is None or ic_value in (None, 0) or u_il is None or u_ic is None or coverage_factor is None:
            return None
        ic_decimal = self._to_decimal(ic_value)
        il_decimal = self._to_decimal(il_value)
        if ic_decimal in (None, 0) or il_decimal is None:
            return None
        term_1 = (Decimal('100') / ic_decimal) * u_il
        term_2 = (Decimal('100') * il_decimal / (ic_decimal * ic_decimal)) * u_ic
        combined_uncertainty = self._sqrt((term_1 * term_1) + (term_2 * term_2))
        if combined_uncertainty is None:
            return None
        return abs(coverage_factor * combined_uncertainty)

    @property
    def expanded_uncertainty_before_pct_auto(self):
        return self._expanded_uncertainty_for_il(self.il_before)

    @property
    def expanded_uncertainty_after_pct_auto(self):
        return self._expanded_uncertainty_for_il(self.il_after)

    @property
    def expanded_uncertainty_calc_pct_auto(self):
        if self.is_level_form:
            return self.level_uncertainty_expanded_m
        if self.is_scanner_form:
            return self.scanner_u_expanded_mm
        if self.is_flow_form:
            return self.flow_max_uncertainty_pct
        if self.is_flow_adjust_form:
            return self.flow_adjust_u_expanded_pct
        if self.is_density_form:
            return self.density_final_u_expanded_pct
        return self.expanded_uncertainty_after_pct_auto

    @property
    def expanded_uncertainty_calc_value(self):
        if self.expanded_uncertainty_calc_pct is not None:
            return self.expanded_uncertainty_calc_pct
        if self.is_level_form:
            return self.level_uncertainty_expanded_m
        if self.is_flow_form:
            return self.flow_max_uncertainty_pct
        if self.is_flow_adjust_form:
            return self.flow_adjust_u_expanded_pct
        if self.is_density_form:
            return self.density_final_u_expanded_pct
        return self.expanded_uncertainty_calc_pct_auto

    @property
    def expanded_uncertainty_is_evaluable(self):
        return self.expanded_uncertainty_calc_value is not None

    @property
    def expanded_uncertainty_ok(self):
        return self.expanded_uncertainty_calc_value is not None

    @property
    def expanded_uncertainty_status_label(self):
        if self.expanded_uncertainty_calc_value is None:
            return 'Pendente dados'
        return 'Calculada'

    @property
    def expanded_uncertainty_status_detail(self):
        unit = self.expanded_uncertainty_unit_label
        if self.expanded_uncertainty_calc_value is None:
            return 'Preencha as medicoes para calcular a incerteza expandida.'
        return f'Incerteza expandida calculada: {self.expanded_uncertainty_calc_value:.2f}{unit}.'

    @property
    def acceptance_limit_pct(self):
        if self.acceptance_criterion_pct is not None:
            return self.acceptance_criterion_pct
        if self.equipment_id:
            if self.form_type_id:
                criteria = self.equipment.criteria_for_form(self.form_type)
                if criteria and criteria.acceptance_criterion_value is not None:
                    return criteria.acceptance_criterion_value
            if self.equipment.acceptance_criterion_pct is not None:
                return self.equipment.acceptance_criterion_pct
        return self.DEFAULT_ACCEPTANCE_LIMIT_PCT

    @property
    def acceptance_unit_label(self):
        if self.acceptance_criterion_unit:
            return self.acceptance_criterion_unit
        if self.equipment_id and self.form_type_id:
            criteria = self.equipment.criteria_for_form(self.form_type)
            if criteria:
                return criteria.acceptance_criterion_unit
        if self.is_level_form:
            return 'm'
        if self.is_scanner_form:
            return 'mm'
        return '%'

    @property
    def expanded_uncertainty_unit_label(self):
        if self.expanded_uncertainty_unit:
            return self.expanded_uncertainty_unit
        if self.equipment_id and self.form_type_id:
            criteria = self.equipment.criteria_for_form(self.form_type)
            if criteria:
                return criteria.expanded_uncertainty_unit
        if self.is_level_form:
            return 'm'
        if self.is_scanner_form:
            return 'mm'
        return '%'

    @property
    def acceptance_error_before_value(self):
        if self.is_level_form:
            return self.level_before_mean_abs_m
        if self.is_scanner_form:
            return self.scanner_max_error_abs_mm
        if self.is_flow_form:
            return self.flow_max_error_abs_pct
        if self.is_flow_adjust_form:
            return self.flow_adjust_error_before_pct_auto
        if self.is_density_form:
            return self.density_before_error_pct
        return self.error_before_pct if self.error_before_pct is not None else self.error_before_pct_auto

    @property
    def acceptance_error_after_value(self):
        if self.is_level_form:
            return self.level_final_mean_abs_m
        if self.is_scanner_form:
            return self.scanner_max_error_abs_mm
        if self.is_flow_form:
            return self.flow_max_error_abs_pct
        if self.is_flow_adjust_form:
            return self.flow_adjust_final_error_pct
        if self.is_density_form:
            return self.density_final_error_pct
        return self.error_after_pct if self.error_after_pct is not None else self.error_after_pct_auto

    @property
    def acceptance_error_after_abs(self):
        value = self.acceptance_error_after_value
        return None if value is None else abs(value)

    @property
    def instrument_error_ok(self):
        value = self.acceptance_error_after_abs
        return value is not None and value <= self.acceptance_limit_pct

    @property
    def instrument_error_status_label(self):
        if self.acceptance_error_after_abs is None:
            return 'Pendente dados'
        return 'Aprovado' if self.instrument_error_ok else 'Reprovado'

    @property
    def acceptance_combined_value(self):
        if self.is_flow_form:
            return self.flow_max_combined_pct
        if self.is_density_form:
            return self.density_final_margin_pct
        error_abs = self.acceptance_error_after_abs
        uncertainty = self.expanded_uncertainty_calc_value
        if error_abs is None or uncertainty is None:
            return None
        return error_abs + abs(uncertainty)

    @property
    def acceptance_is_evaluable(self):
        if self.is_flow_form:
            return self.flow_status in {'Aprovado', 'Reprovado'}
        if self.is_density_form:
            return self.density_final_margin_pct is not None and self.density_scale_ok is not None
        return self.acceptance_combined_value is not None

    @property
    def acceptance_ok(self):
        if self.is_flow_form:
            return self.flow_status == 'Aprovado'
        if self.is_density_form:
            if self.density_scale_ok is not True:
                return False
            combined = self.density_final_margin_pct
            if combined is None:
                return False
            return combined <= self.acceptance_limit_pct
        combined = self.acceptance_combined_value
        if combined is None:
            return False
        return combined <= self.acceptance_limit_pct

    @property
    def acceptance_status_label(self):
        if self.is_flow_form:
            return self.flow_status
        if self.is_density_form:
            if self.density_scale_ok is False:
                return 'Inválido - balança NOK'
            if not self.acceptance_is_evaluable:
                return 'Pendente dados'
            return 'Aprovado' if self.acceptance_ok else 'Reprovado'
        if not self.acceptance_is_evaluable:
            return 'Pendente dados'
        return 'Aprovado' if self.acceptance_ok else 'Reprovado'

    @property
    def acceptance_block_reason(self):
        unit = self.acceptance_unit_label
        if self.is_flow_form:
            if not self.flow_valid_points:
                return (
                    'Validação final bloqueada: preencha ao menos um ponto com tendência e U(e) '
                    'para avaliar o certificado.'
                )
            if self.acceptance_ok:
                return ''
            return (
                'Validação final bloqueada: há ponto(s) com soma |erro| + U(e) acima do critério de aceitação '
                f'(<= {self.acceptance_limit_pct:.2f}{unit}).'
            )
        if self.is_density_form:
            if self.density_scale_ok is None:
                return (
                    'Validação final bloqueada: preencha MAB e MIB da balança para avaliar '
                    'o pré-requisito da checagem da balança.'
                )
            if self.density_scale_ok is False:
                return (
                    'Validação final bloqueada: checagem da balança estática em NOK. '
                    'Corrija a balança antes de validar o densímetro.'
                )
            if self.density_final_margin_pct is None:
                return (
                    'Validação final bloqueada: dados insuficientes para calcular '
                    'erro e incerteza expandida do densímetro.'
                )
            if self.acceptance_ok:
                return ''
            return (
                'Validação final bloqueada: soma |erro final| + U(e) acima do critério de aceitação '
                f'(<= {self.acceptance_limit_pct:.2f}{unit}). '
                f'Valor atual: {self.density_final_margin_pct:.2f}{unit}.'
            )
        if self.acceptance_error_after_abs is None:
            return (
                'Critério de aceitação não pode ser avaliado. '
                f'Preencha os dados necessários para calcular o erro final ({unit}).'
            )
        if self.expanded_uncertainty_calc_value is None:
            return (
                'Validação final bloqueada: incerteza expandida calculada indisponível. '
                'Preencha as medições para calcular U(e).'
            )
        if self.acceptance_ok:
            return ''
        combined = self.acceptance_combined_value
        return (
            'Validação final bloqueada: soma |erro final| + U(e) acima do critério de aceitação '
            f'(<= {self.acceptance_limit_pct:.2f}{unit}). Valor atual: {combined:.2f}{unit}.'
        )

    def save(self, *args, **kwargs):
        configured_criteria = None
        if self.equipment_id:
            if self.form_type_id is None:
                self.form_type = self.equipment.available_form_types.first()
            if self.form_type_id:
                configured_criteria = self.equipment.criteria_for_form(self.form_type)
            if self._state.adding and configured_criteria:
                if configured_criteria.acceptance_criterion_value is not None:
                    self.acceptance_criterion_pct = configured_criteria.acceptance_criterion_value
                self.acceptance_criterion_unit = configured_criteria.acceptance_criterion_unit
                self.expanded_uncertainty_unit = configured_criteria.expanded_uncertainty_unit
            elif self._state.adding:
                if self.acceptance_criterion_pct is None:
                    self.acceptance_criterion_pct = self.equipment.acceptance_criterion_pct
                default_unit = self.equipment.acceptance_criterion_unit or EquipmentFormCriteria.Unit.PERCENT
                self.acceptance_criterion_unit = self.acceptance_criterion_unit or default_unit
                self.expanded_uncertainty_unit = self.expanded_uncertainty_unit or default_unit
        if self.is_scanner_form:
            if self.acceptance_criterion_unit == EquipmentFormCriteria.Unit.PERCENT:
                self.acceptance_criterion_unit = EquipmentFormCriteria.Unit.MILLIMETER
            if self.expanded_uncertainty_unit == EquipmentFormCriteria.Unit.PERCENT:
                self.expanded_uncertainty_unit = EquipmentFormCriteria.Unit.MILLIMETER
        if self.is_flow_form:
            if not self.acceptance_criterion_unit:
                self.acceptance_criterion_unit = EquipmentFormCriteria.Unit.PERCENT
            if not self.expanded_uncertainty_unit:
                self.expanded_uncertainty_unit = EquipmentFormCriteria.Unit.PERCENT
        if self.is_flow_adjust_form:
            if not self.acceptance_criterion_unit:
                self.acceptance_criterion_unit = EquipmentFormCriteria.Unit.PERCENT
            if not self.expanded_uncertainty_unit:
                self.expanded_uncertainty_unit = EquipmentFormCriteria.Unit.PERCENT
        if self.is_density_form:
            if not self.acceptance_criterion_unit:
                self.acceptance_criterion_unit = EquipmentFormCriteria.Unit.PERCENT
            if not self.expanded_uncertainty_unit:
                self.expanded_uncertainty_unit = EquipmentFormCriteria.Unit.PERCENT
        if self.is_level_form:
            self.error_before_pct = self.level_before_mean_abs_m
            self.error_after_pct = self.level_final_mean_abs_m
            self.expanded_uncertainty_calc_pct = self.level_uncertainty_expanded_m
        elif self.is_scanner_form:
            self.error_before_pct = self.scanner_max_error_abs_mm
            self.error_after_pct = self.scanner_max_error_abs_mm
            self.expanded_uncertainty_calc_pct = self.scanner_u_expanded_mm
        elif self.is_flow_form:
            self.error_before_pct = self.flow_max_error_abs_pct
            self.error_after_pct = self.flow_max_error_abs_pct
            self.expanded_uncertainty_calc_pct = self.flow_max_uncertainty_pct
        elif self.is_flow_adjust_form:
            self.error_before_pct = self.flow_adjust_error_before_pct_auto
            self.error_after_pct = self.flow_adjust_final_error_pct
            self.expanded_uncertainty_calc_pct = self.flow_adjust_u_expanded_pct
        elif self.is_density_form:
            self.error_before_pct = self.density_before_error_pct
            self.error_after_pct = self.density_final_error_pct
            self.expanded_uncertainty_calc_pct = self.density_final_u_expanded_pct
        else:
            self.ibm = self.ibm_auto
            self.mark_distance = self.mark_distance_auto
            self.belt_speed_v = self.belt_speed_v_auto
            self.belt_length = self.belt_length_auto
            self.speed_characteristic_b04 = self.speed_characteristic_b04_auto
            self.calculated_flow_ic = self.calculated_flow_ic_auto
            self.error_before_pct = self.error_before_pct_auto
            self.error_after_pct = self.error_after_pct_auto
            self.expanded_uncertainty_calc_pct = self.expanded_uncertainty_calc_pct_auto
        super().save(*args, **kwargs)


class PortalNotification(models.Model):
    class Category(models.TextChoices):
        FORM_PENDING_VALIDATION = 'form_pending_validation', 'Formulário pendente validação'
        FORM_APPROVED = 'form_approved', 'Formulário aprovado'
        FORM_REWORK = 'form_rework', 'Formulário para refazer'
        DEADLINE_ALERT = 'deadline_alert', 'Alerta de prazo'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='portal_notifications',
        verbose_name='Usuário',
    )
    category = models.CharField('Categoria', max_length=40, choices=Category.choices)
    title = models.CharField('Título', max_length=200)
    message = models.TextField('Mensagem')
    submission = models.ForeignKey(
        FormSubmission,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='notifications',
        verbose_name='Formulário',
    )
    equipment = models.ForeignKey(
        Equipment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='notifications',
        verbose_name='Equipamento',
    )
    dedupe_key = models.CharField(
        'Chave de deduplicação',
        max_length=180,
        blank=True,
        default='',
    )
    is_read = models.BooleanField('Lida', default=False)
    email_sent_at = models.DateTimeField('E-mail enviado em', null=True, blank=True)
    created_at = models.DateTimeField('Criada em', auto_now_add=True)
    updated_at = models.DateTimeField('Atualizada em', auto_now=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'dedupe_key'],
                condition=~Q(dedupe_key=''),
                name='unique_notification_dedupe_per_user',
            )
        ]

    def __str__(self):
        return f'{self.user.username}: {self.title}'


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def ensure_portal_access_for_new_user(sender, instance, created, **kwargs):
    if created:
        PortalUserAccess.objects.get_or_create(
            user=instance,
            defaults={
                'registration': instance.username,
                'role': PortalUserAccess.Role.MASTER if instance.is_superuser else PortalUserAccess.Role.VIEWER,
            },
        )



