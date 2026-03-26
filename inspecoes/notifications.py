from __future__ import annotations

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from .models import Equipment, FormSubmission, PortalNotification, PortalUserAccess


def _is_email_notification_enabled():
    return str(getattr(settings, 'PORTAL_NOTIFY_EMAIL_ENABLED', 'false')).lower() == 'true'


def _send_email_if_enabled(user, subject, message):
    if not _is_email_notification_enabled():
        return None
    recipient = (getattr(user, 'email', '') or '').strip()
    if not recipient:
        return None
    send_mail(
        subject=subject,
        message=message,
        from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', None),
        recipient_list=[recipient],
        fail_silently=True,
    )
    return timezone.now()


def create_portal_notification(*, user, category, title, message, submission=None, equipment=None, dedupe_key=''):
    if not dedupe_key:
        notification = PortalNotification.objects.create(
            user=user,
            category=category,
            title=title,
            message=message,
            submission=submission,
            equipment=equipment,
            dedupe_key='',
        )
        email_sent_at = _send_email_if_enabled(user, title, message)
        if email_sent_at:
            notification.email_sent_at = email_sent_at
            notification.save(update_fields=['email_sent_at', 'updated_at'])
        return notification

    notification, created = PortalNotification.objects.get_or_create(
        user=user,
        dedupe_key=dedupe_key,
        defaults={
            'category': category,
            'title': title,
            'message': message,
            'submission': submission,
            'equipment': equipment,
        },
    )
    if created:
        email_sent_at = _send_email_if_enabled(user, title, message)
        if email_sent_at:
            notification.email_sent_at = email_sent_at
            notification.save(update_fields=['email_sent_at', 'updated_at'])
        return notification

    changed = False
    if notification.title != title:
        notification.title = title
        changed = True
    if notification.message != message:
        notification.message = message
        changed = True
    if notification.category != category:
        notification.category = category
        changed = True
    if submission and notification.submission_id != submission.id:
        notification.submission = submission
        changed = True
    if equipment and notification.equipment_id != equipment.id:
        notification.equipment = equipment
        changed = True
    if notification.is_read:
        notification.is_read = False
        changed = True
    if changed:
        notification.save()
    return notification


def _validation_deadline_message_line(submission: FormSubmission):
    due_date = submission.validation_due_date_local
    if not due_date:
        return 'Prazo de validação: não configurado.'
    return (
        f'Prazo de validação: até {due_date.strftime("%d/%m/%Y")} '
        f'({submission.validation_deadline_status_label.lower()}).'
    )


def notify_validators_submission_pending(submission: FormSubmission, actor_user=None):
    designated_user = submission.assigned_validator
    if designated_user and designated_user.is_active:
        recipients = [designated_user]
    else:
        accesses = PortalUserAccess.objects.select_related('user').filter(
            role__in=[PortalUserAccess.Role.VALIDATOR, PortalUserAccess.Role.MASTER],
            user__is_active=True,
        )
        recipients = [access.user for access in accesses]
    for recipient in recipients:
        if actor_user and recipient.pk == actor_user.pk:
            continue
        title = f'Novo formulário pendente de validação - OM {submission.om_number}'
        message = (
            f'Formulário #{submission.id} pendente de validação.\n'
            f'Equipamento: {submission.equipment.tag} - {submission.equipment.description}\n'
            f'Local: {submission.location_snapshot}\n'
            f'Executor: {submission.executor_name}\n'
            f'Data da visita: {submission.execution_date}\n'
            f'Validador designado: {submission.assigned_validator_label}\n'
            f'{_validation_deadline_message_line(submission)}\n'
        )
        dedupe_key = f'form_pending_validation:{submission.id}:{recipient.pk}:{submission.updated_at:%Y%m%d%H%M%S}'
        create_portal_notification(
            user=recipient,
            category=PortalNotification.Category.FORM_PENDING_VALIDATION,
            title=title,
            message=message,
            submission=submission,
            equipment=submission.equipment,
            dedupe_key=dedupe_key,
        )


def notify_technician_validation_result(
    submission: FormSubmission,
    *,
    approved: bool,
    validator_user=None,
    feedback='',
):
    recipient = submission.created_by
    if not recipient or not recipient.is_active:
        return

    if approved:
        category = PortalNotification.Category.FORM_APPROVED
        title = f'Formulário validado - OM {submission.om_number}'
        message = (
            f'O formulário #{submission.id} foi validado.\n'
            f'Equipamento: {submission.equipment.tag}\n'
            f'Validador: {submission.validator_name or "-"}\n'
            f'Status: Aprovado\n'
            f'{_validation_deadline_message_line(submission)}\n'
        )
    else:
        category = PortalNotification.Category.FORM_REWORK
        title = f'Formulário reprovado - Refazer visita - OM {submission.om_number}'
        message = (
            f'O formulário #{submission.id} foi reprovado e precisa de nova visita.\n'
            f'Equipamento: {submission.equipment.tag}\n'
            f'Validador: {submission.validator_name or "-"}\n'
            f'Motivo: {feedback or "Sem observação."}\n'
            f'{_validation_deadline_message_line(submission)}\n'
        )

    dedupe_key = f'form_validation_result:{submission.id}:{submission.updated_at:%Y%m%d%H%M%S}'
    create_portal_notification(
        user=recipient,
        category=category,
        title=title,
        message=message,
        submission=submission,
        equipment=submission.equipment,
        dedupe_key=dedupe_key,
    )


def sync_deadline_notifications_for_user(user):
    access = PortalUserAccess.for_user(user)
    if not access or not access.can_receive_deadline_notifications_portal:
        return
    user_email = (getattr(user, 'email', '') or '').strip().lower()
    if not user_email:
        return

    equipments = Equipment.objects.filter(active=True).order_by('tag')
    for equipment in equipments:
        if equipment.deadline_status_code not in {'due_soon', 'overdue'}:
            continue
        recipients = {mail.strip().lower() for mail in equipment.notification_recipients}
        if user_email not in recipients:
            continue
        title = f'Prazo do equipamento {equipment.tag}: {equipment.deadline_status_label}'
        message = (
            f'Equipamento: {equipment.tag} - {equipment.description}\n'
            f'Local: {equipment.location}\n'
            f'Status do prazo: {equipment.deadline_status_label}\n'
            f'Detalhe: {equipment.deadline_status_detail}\n'
            f'Última visita: {equipment.last_visit_date or "-"}\n'
            f'Próxima visita: {equipment.next_visit_due_date or "-"}\n'
        )
        dedupe_key = (
            f'deadline_alert:{equipment.id}:{equipment.deadline_status_code}:{equipment.next_visit_due_date or "-"}'
        )
        create_portal_notification(
            user=user,
            category=PortalNotification.Category.DEADLINE_ALERT,
            title=title,
            message=message,
            equipment=equipment,
            dedupe_key=dedupe_key,
        )

