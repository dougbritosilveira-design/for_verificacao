import re
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone
from django.utils.functional import cached_property


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
    def access_label(self):
        if self.is_master_portal:
            return self.Role.MASTER.label
        return dict(self.Role.choices).get(self.role, 'Sem acesso')

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


class Equipment(models.Model):
    tag = models.CharField('TAG', max_length=80, unique=True)
    description = models.CharField('Descrição', max_length=255)
    location = models.CharField('Local', max_length=255)
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
        'Critério de aceitação (%)',
        max_digits=6,
        decimal_places=1,
        default=Decimal('1.0'),
        validators=[MinValueValidator(Decimal('0.001'))],
        help_text='Limite de aceitação para o erro final (%). Ex.: 1,0',
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


class FormSubmission(models.Model):
    DEFAULT_ACCEPTANCE_LIMIT_PCT = Decimal('1.0')

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
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_submissions',
        verbose_name='Criado por',
    )
    location_snapshot = models.CharField(max_length=255)
    om_number = models.CharField('Nº OM', max_length=50)
    execution_date = models.DateField(default=timezone.localdate)
    executor_name = models.CharField(max_length=120)
    acceptance_criterion_pct = models.DecimalField(
        'Critério de aceitação (%)',
        max_digits=6,
        decimal_places=1,
        default=Decimal('1.0'),
    )
    expanded_uncertainty_pct = models.DecimalField(
        'Incerteza expandida (%)',
        max_digits=6,
        decimal_places=3,
        null=True,
        blank=True,
    )

    t1 = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    t2 = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    t3 = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    m1 = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    m2 = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    m3 = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
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
        return f'OM {self.om_number} - {self.equipment.tag}'

    @staticmethod
    def _avg(*values):
        valid = [v for v in values if v is not None]
        if not valid:
            return None
        return sum(valid) / Decimal(len(valid))

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
        if self.mark_distance is None or self.md in (None, 0):
            return None
        return self.mark_distance / self.md

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
    def acceptance_limit_pct(self):
        if self.acceptance_criterion_pct is not None:
            return self.acceptance_criterion_pct
        if self.equipment_id and self.equipment.acceptance_criterion_pct is not None:
            return self.equipment.acceptance_criterion_pct
        return self.DEFAULT_ACCEPTANCE_LIMIT_PCT

    @property
    def acceptance_error_before_value(self):
        return self.error_before_pct if self.error_before_pct is not None else self.error_before_pct_auto

    @property
    def acceptance_error_after_value(self):
        return self.error_after_pct if self.error_after_pct is not None else self.error_after_pct_auto

    @property
    def acceptance_error_after_abs(self):
        value = self.acceptance_error_after_value
        return None if value is None else abs(value)

    @property
    def acceptance_is_evaluable(self):
        return self.acceptance_error_after_value is not None

    @property
    def acceptance_ok(self):
        value = self.acceptance_error_after_abs
        return value is not None and value <= self.acceptance_limit_pct

    @property
    def acceptance_status_label(self):
        if not self.acceptance_is_evaluable:
            return 'Pendente dados'
        return 'Aprovado' if self.acceptance_ok else 'Reprovado'

    @property
    def acceptance_block_reason(self):
        if not self.acceptance_is_evaluable:
            return (
                'Critério de aceitação não pode ser avaliado. '
                'Preencha os dados necessários para calcular o erro final (%).'
            )
        if self.acceptance_ok:
            return ''
        return (
            f'Validação final bloqueada: erro final fora do critério de aceitação '
            f'(<= {self.acceptance_limit_pct}%). Valor atual: {self.acceptance_error_after_value:.3f}%.'
        )

    def save(self, *args, **kwargs):
        if self.equipment_id:
            if self.acceptance_criterion_pct is None:
                self.acceptance_criterion_pct = self.equipment.acceptance_criterion_pct
            if self.expanded_uncertainty_pct is None:
                self.expanded_uncertainty_pct = self.equipment.expanded_uncertainty_pct
        self.ibm = self.ibm_auto
        self.belt_speed_v = self.belt_speed_v_auto
        self.belt_length = self.belt_length_auto
        self.speed_characteristic_b04 = self.speed_characteristic_b04_auto
        self.calculated_flow_ic = self.calculated_flow_ic_auto
        self.error_before_pct = self.error_before_pct_auto
        self.error_after_pct = self.error_after_pct_auto
        super().save(*args, **kwargs)


class PortalNotification(models.Model):
    class Category(models.TextChoices):
        FORM_PENDING_VALIDATION = 'form_pending_validation', 'Formulário pendente validação'
        FORM_APPROVED = 'form_approved', 'Formulário aprovado'
        FORM_REWORK = 'form_rework', 'Formulário para refação'
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
