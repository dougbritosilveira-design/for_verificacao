from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .forms import SelectionForm, TechnicalForm, ValidationForm
from .models import Equipment, FormSubmission, PortalNotification, PortalUserAccess
from .notifications import (
    notify_technician_validation_result,
    notify_validators_submission_pending,
    sync_deadline_notifications_for_user,
)
from .services import build_submission_pdf_filename, generate_submission_pdf_bytes, process_sap_submission


def _pdf_download_response(submission):
    pdf_bytes = generate_submission_pdf_bytes(submission)
    filename = build_submission_pdf_filename(submission)
    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


def _access_for_user(user):
    return PortalUserAccess.for_user(user)


def _can_view(user, screen):
    access = _access_for_user(user)
    if not access:
        return user.is_superuser
    if screen == 'forms':
        return access.can_view_forms_portal
    if screen == 'history':
        return access.can_view_history_portal
    if screen == 'deadlines':
        return access.can_view_deadlines_portal
    if screen == 'notifications':
        return access.can_view_notifications_portal
    return False


def _can_create_forms(user):
    access = _access_for_user(user)
    if not access:
        return user.is_superuser
    return access.can_create_forms_portal


def _can_edit_forms(user):
    access = _access_for_user(user)
    if not access:
        return user.is_superuser
    return access.can_edit_forms_portal


def _can_validate_forms(user):
    access = _access_for_user(user)
    if not access:
        return user.is_superuser
    return access.can_validate_forms_portal


def _can_send_sap(user):
    access = _access_for_user(user)
    if not access:
        return user.is_superuser
    return access.can_send_sap_portal


def _redirect_first_allowed(request):
    user = request.user
    access = _access_for_user(user)
    if access:
        if access.role == PortalUserAccess.Role.TECHNICIAN and _can_view(user, 'forms') and _can_create_forms(user):
            return redirect('inspecoes:selection')
        if access.role == PortalUserAccess.Role.VALIDATOR and _can_view(user, 'notifications'):
            return redirect('inspecoes:notifications')
        if access.role == PortalUserAccess.Role.VIEWER and _can_view(user, 'history'):
            return redirect('inspecoes:history')
        if access.role == PortalUserAccess.Role.MASTER and _can_view(user, 'history'):
            return redirect('inspecoes:history')
    if _can_view(user, 'forms') and _can_create_forms(user):
        return redirect('inspecoes:selection')
    if _can_view(user, 'notifications'):
        return redirect('inspecoes:notifications')
    if _can_view(user, 'history'):
        return redirect('inspecoes:history')
    if _can_view(user, 'deadlines'):
        return redirect('inspecoes:equipment-deadlines')
    if _can_view(user, 'forms'):
        return redirect('inspecoes:history')
    logout(request)
    messages.error(request, 'Seu usuário não possui telas liberadas no portal. Procure o administrador.')
    return redirect('login')


def _deny_screen_access(request, screen_label):
    messages.warning(request, f'Seu usuário não possui permissão para acessar a tela "{screen_label}".')
    return _redirect_first_allowed(request)


def _deny_create_access(request):
    messages.warning(request, 'Seu usuário não possui permissão para criar formulários.')
    return _redirect_first_allowed(request)


def _deny_edit_access(request):
    messages.warning(request, 'Seu usuário não possui permissão para editar formulários.')
    return _redirect_first_allowed(request)


def _deny_validate_access(request):
    messages.warning(request, 'Seu usuário não possui permissão para validar formulários.')
    return _redirect_first_allowed(request)


def _deny_send_sap_access(request):
    messages.warning(request, 'Seu usuário não possui permissão para enviar anexo para SAP.')
    return _redirect_first_allowed(request)


@login_required
def home_view(request):
    return _redirect_first_allowed(request)


@login_required
def selection_view(request):
    if not _can_view(request.user, 'forms'):
        return _deny_screen_access(request, 'Formulários')
    if not _can_create_forms(request.user):
        return _deny_create_access(request)

    equipment_qs = Equipment.objects.filter(active=True).order_by('tag')
    equipment_locations = {str(e.pk): e.location for e in equipment_qs}

    if request.method == 'POST':
        form = SelectionForm(request.POST)
        form.fields['equipment'].queryset = equipment_qs
        if form.is_valid():
            submission = form.save(commit=False)
            submission.created_by = request.user
            submission.acceptance_criterion_pct = submission.equipment.acceptance_criterion_pct
            submission.expanded_uncertainty_pct = submission.equipment.expanded_uncertainty_pct
            if not submission.location_snapshot:
                submission.location_snapshot = submission.equipment.location
            submission.status = FormSubmission.Status.DRAFT
            submission.save()
            messages.success(request, 'Formulário criado. Preencha os dados técnicos.')
            return redirect('inspecoes:form-edit', pk=submission.pk)
    else:
        form = SelectionForm()
        form.fields['equipment'].queryset = equipment_qs
        equipment_id = request.GET.get('equipment')
        if equipment_id:
            try:
                equipment = Equipment.objects.get(pk=equipment_id)
            except Equipment.DoesNotExist:
                equipment = None
            if equipment:
                form.fields['equipment'].initial = equipment.pk
                form.fields['location_snapshot'].initial = equipment.location
    return render(
        request,
        'inspecoes/selection.html',
        {
            'form': form,
            'equipment_locations': equipment_locations,
        },
    )


@login_required
def form_edit_view(request, pk):
    if not _can_view(request.user, 'forms'):
        return _deny_screen_access(request, 'Formulários')
    if not _can_edit_forms(request.user):
        return _deny_edit_access(request)

    submission = get_object_or_404(FormSubmission.objects.select_related('equipment', 'created_by'), pk=pk)
    if submission.status in [FormSubmission.Status.APPROVED, FormSubmission.Status.SENT_TO_SAP]:
        messages.warning(request, 'Formulário já validado. Edição bloqueada.')
        return redirect('inspecoes:detail', pk=submission.pk)

    if request.method == 'POST':
        form = TechnicalForm(request.POST, instance=submission)
        if form.is_valid():
            previous_status = submission.status
            submission = form.save(commit=False)
            if submission.status in [FormSubmission.Status.DRAFT, FormSubmission.Status.REWORK_REQUIRED]:
                submission.status = FormSubmission.Status.PENDING_VALIDATION
            submission.save()

            if previous_status != FormSubmission.Status.PENDING_VALIDATION and submission.status == FormSubmission.Status.PENDING_VALIDATION:
                notify_validators_submission_pending(submission, actor_user=request.user)

            messages.success(request, 'Formulário salvo.')
            if 'go_validate' in request.POST:
                if _can_validate_forms(request.user):
                    return redirect('inspecoes:form-validate', pk=submission.pk)
                messages.info(request, 'Formulário enviado para a fila de validação.')
                return redirect('inspecoes:detail', pk=submission.pk)
            return redirect('inspecoes:form-edit', pk=submission.pk)
    else:
        form = TechnicalForm(instance=submission)
    return render(request, 'inspecoes/form_edit.html', {'form': form, 'submission': submission})


@login_required
def form_validate_view(request, pk):
    if not _can_view(request.user, 'forms'):
        return _deny_screen_access(request, 'Formulários')
    if not _can_validate_forms(request.user):
        return _deny_validate_access(request)

    submission = get_object_or_404(FormSubmission.objects.select_related('equipment', 'created_by'), pk=pk)
    if submission.status in [FormSubmission.Status.APPROVED, FormSubmission.Status.SENT_TO_SAP]:
        messages.info(request, 'Formulário já validado.')
        return redirect('inspecoes:detail', pk=submission.pk)

    if request.method == 'POST':
        form = ValidationForm(request.POST)
        if form.is_valid():
            decision = form.cleaned_data['decision']
            feedback = (form.cleaned_data.get('feedback') or '').strip()
            submission.validator_name = form.cleaned_data['validator_name']
            submission.validator_signature_data = form.cleaned_data['signature_data']
            submission.validated_at = timezone.now()
            submission.validation_feedback = feedback
            submission.sap_status = FormSubmission.SapStatus.NOT_STARTED
            submission.sap_attachment_id = ''
            submission.sap_response_message = ''
            submission.sap_sent_at = None

            if decision == ValidationForm.DecisionChoices.APPROVE:
                if not submission.acceptance_ok:
                    form.add_error(None, submission.acceptance_block_reason)
                    return render(
                        request,
                        'inspecoes/validation.html',
                        {'form': form, 'submission': submission},
                    )
                submission.status = FormSubmission.Status.APPROVED
                submission.sap_response_message = 'Aguardando envio manual para SAP.'
                submission.save(
                    update_fields=[
                        'validator_name',
                        'validator_signature_data',
                        'validated_at',
                        'validation_feedback',
                        'status',
                        'sap_status',
                        'sap_attachment_id',
                        'sap_response_message',
                        'sap_sent_at',
                        'updated_at',
                    ]
                )
                notify_technician_validation_result(submission, approved=True, validator_user=request.user, feedback=feedback)
                messages.success(request, 'Formulário validado com sucesso.')
                return _pdf_download_response(submission)

            submission.status = FormSubmission.Status.REWORK_REQUIRED
            submission.save(
                update_fields=[
                    'validator_name',
                    'validator_signature_data',
                    'validated_at',
                    'validation_feedback',
                    'status',
                    'sap_status',
                    'sap_attachment_id',
                    'sap_response_message',
                    'sap_sent_at',
                    'updated_at',
                ]
            )
            notify_technician_validation_result(submission, approved=False, validator_user=request.user, feedback=feedback)
            messages.warning(request, 'Refação solicitada para o técnico responsável.')
            return redirect('inspecoes:detail', pk=submission.pk)
    else:
        initial_name = request.user.get_full_name().strip() or request.user.username
        form = ValidationForm(initial={'validator_name': initial_name})
    return render(request, 'inspecoes/validation.html', {'form': form, 'submission': submission})


@login_required
def form_download_pdf_view(request, pk):
    if not (_can_view(request.user, 'forms') or _can_view(request.user, 'history')):
        return _deny_screen_access(request, 'Detalhe/PDF do formulário')

    submission = get_object_or_404(FormSubmission.objects.select_related('equipment'), pk=pk)
    if submission.status not in [FormSubmission.Status.APPROVED, FormSubmission.Status.SENT_TO_SAP]:
        messages.warning(request, 'Valide o formulário antes de baixar o PDF final.')
        if _can_validate_forms(request.user):
            return redirect('inspecoes:form-validate', pk=submission.pk)
        return redirect('inspecoes:detail', pk=submission.pk)
    return _pdf_download_response(submission)


@login_required
def form_send_sap_view(request, pk):
    if not _can_view(request.user, 'forms'):
        return _deny_screen_access(request, 'Formulários')
    if not _can_send_sap(request.user):
        return _deny_send_sap_access(request)

    submission = get_object_or_404(FormSubmission.objects.select_related('equipment'), pk=pk)
    if request.method != 'POST':
        return redirect('inspecoes:detail', pk=submission.pk)

    if submission.status not in [FormSubmission.Status.APPROVED, FormSubmission.Status.SENT_TO_SAP]:
        messages.warning(request, 'Valide o formulário antes de enviar para o SAP.')
        return redirect('inspecoes:detail', pk=submission.pk)

    if not submission.acceptance_ok:
        messages.error(request, submission.acceptance_block_reason)
        return redirect('inspecoes:detail', pk=submission.pk)

    messages.info(request, 'Enviando anexo para SAP...')
    process_sap_submission(submission)
    submission.refresh_from_db()
    if submission.sap_status == FormSubmission.SapStatus.SUCCESS:
        messages.success(request, 'Anexo enviado para o SAP com sucesso.')
    else:
        messages.warning(request, f'Falha no envio para SAP: {submission.sap_response_message}')
    return redirect('inspecoes:detail', pk=submission.pk)


@login_required
def history_view(request):
    if not _can_view(request.user, 'history'):
        return _deny_screen_access(request, 'Histórico')

    qs = FormSubmission.objects.select_related('equipment').all()
    status = request.GET.get('status')
    tag = request.GET.get('tag')
    om = request.GET.get('om')
    if status:
        qs = qs.filter(status=status)
    if tag:
        qs = qs.filter(equipment__tag__icontains=tag)
    if om:
        qs = qs.filter(om_number__icontains=om)
    return render(request, 'inspecoes/history.html', {
        'submissions': qs[:200],
        'status_choices': FormSubmission.Status.choices,
        'filters': {'status': status or '', 'tag': tag or '', 'om': om or ''},
    })


@login_required
def equipment_deadlines_view(request):
    if not _can_view(request.user, 'deadlines'):
        return _deny_screen_access(request, 'Prazos')

    tag = (request.GET.get('tag') or '').strip()
    status_filter = (request.GET.get('deadline_status') or '').strip()
    active_only = (request.GET.get('active_only') or '1') == '1'

    equipments_qs = Equipment.objects.all().order_by('tag')
    if active_only:
        equipments_qs = equipments_qs.filter(active=True)
    if tag:
        equipments_qs = equipments_qs.filter(tag__icontains=tag)

    equipments = list(equipments_qs)
    if status_filter:
        equipments = [e for e in equipments if e.deadline_status_code == status_filter]

    return render(
        request,
        'inspecoes/equipment_deadlines.html',
        {
            'equipments': equipments,
            'filters': {
                'tag': tag,
                'deadline_status': status_filter,
                'active_only': '1' if active_only else '0',
            },
            'deadline_status_choices': [
                ('on_time', 'Dentro do prazo'),
                ('due_soon', 'Próximo do vencimento'),
                ('overdue', 'Vencido / atrasado'),
                ('no_history', 'Sem histórico'),
                ('not_configured', 'Não configurado'),
            ],
        },
    )


@login_required
def notifications_view(request):
    if not _can_view(request.user, 'notifications'):
        return _deny_screen_access(request, 'Notificações')

    sync_deadline_notifications_for_user(request.user)

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'mark_all_read':
            PortalNotification.objects.filter(user=request.user, is_read=False).update(
                is_read=True,
                updated_at=timezone.now(),
            )
            messages.success(request, 'Todas as notificações foram marcadas como lidas.')
            return redirect('inspecoes:notifications')
        if action == 'mark_read':
            notification_id = request.POST.get('notification_id')
            if notification_id:
                PortalNotification.objects.filter(
                    user=request.user,
                    pk=notification_id,
                ).update(
                    is_read=True,
                    updated_at=timezone.now(),
                )
                messages.success(request, 'Notificação marcada como lida.')
                return redirect('inspecoes:notifications')

    notifications = PortalNotification.objects.filter(user=request.user).select_related(
        'submission',
        'equipment',
    )[:200]
    return render(
        request,
        'inspecoes/notifications.html',
        {'notifications': notifications},
    )


@login_required
def detail_view(request, pk):
    if not (_can_view(request.user, 'forms') or _can_view(request.user, 'history')):
        return _deny_screen_access(request, 'Detalhe do formulário')
    submission = get_object_or_404(FormSubmission.objects.select_related('equipment', 'created_by'), pk=pk)
    return render(request, 'inspecoes/detail.html', {'submission': submission})
