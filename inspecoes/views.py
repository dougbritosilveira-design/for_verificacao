from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .forms import SelectionForm, TechnicalForm, ValidationForm
from .models import Equipment, FormSubmission, PortalUserAccess
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
    return False


def _can_edit_forms(user):
    access = _access_for_user(user)
    if not access:
        return user.is_superuser
    return access.can_edit_forms_portal


def _redirect_first_allowed(request):
    user = request.user
    if _can_view(user, 'history'):
        return redirect('inspecoes:history')
    if _can_view(user, 'deadlines'):
        return redirect('inspecoes:equipment-deadlines')
    if _can_view(user, 'forms') and _can_edit_forms(user):
        return redirect('inspecoes:selection')
    logout(request)
    messages.error(request, 'Seu usuário não possui telas liberadas no portal. Procure o administrador.')
    return redirect('login')


def _deny_screen_access(request, screen_label):
    messages.warning(request, f'Seu usuário não possui permissão para acessar a tela "{screen_label}".')
    return _redirect_first_allowed(request)


def _deny_edit_access(request):
    messages.warning(request, 'Seu usuário não possui permissão de edição.')
    return _redirect_first_allowed(request)


@login_required
def home_view(request):
    return _redirect_first_allowed(request)


@login_required
def selection_view(request):
    if not _can_view(request.user, 'forms'):
        return _deny_screen_access(request, 'Formulários')
    if not _can_edit_forms(request.user):
        return _deny_edit_access(request)

    equipment_qs = Equipment.objects.filter(active=True).order_by('tag')
    equipment_locations = {str(e.pk): e.location for e in equipment_qs}

    if request.method == 'POST':
        form = SelectionForm(request.POST)
        form.fields['equipment'].queryset = equipment_qs
        if form.is_valid():
            submission = form.save(commit=False)
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

    submission = get_object_or_404(FormSubmission.objects.select_related('equipment'), pk=pk)
    if request.method == 'POST':
        form = TechnicalForm(request.POST, instance=submission)
        if form.is_valid():
            submission = form.save(commit=False)
            if submission.status == FormSubmission.Status.DRAFT:
                submission.status = FormSubmission.Status.PENDING_VALIDATION
            submission.save()
            messages.success(request, 'Formulário salvo.')
            if 'go_validate' in request.POST:
                return redirect('inspecoes:form-validate', pk=submission.pk)
            return redirect('inspecoes:form-edit', pk=submission.pk)
    else:
        form = TechnicalForm(instance=submission)
    return render(request, 'inspecoes/form_edit.html', {'form': form, 'submission': submission})


@login_required
def form_validate_view(request, pk):
    if not _can_view(request.user, 'forms'):
        return _deny_screen_access(request, 'Formulários')
    if not _can_edit_forms(request.user):
        return _deny_edit_access(request)

    submission = get_object_or_404(FormSubmission.objects.select_related('equipment'), pk=pk)
    if request.method == 'POST':
        form = ValidationForm(request.POST)
        if form.is_valid():
            if not submission.acceptance_ok:
                form.add_error(None, submission.acceptance_block_reason)
                return render(
                    request,
                    'inspecoes/validation.html',
                    {'form': form, 'submission': submission},
                )
            submission.validator_name = form.cleaned_data['validator_name']
            submission.validator_signature_data = form.cleaned_data['signature_data']
            submission.validated_at = timezone.now()
            submission.status = FormSubmission.Status.APPROVED
            submission.sap_status = FormSubmission.SapStatus.NOT_STARTED
            submission.sap_attachment_id = ''
            submission.sap_response_message = 'Aguardando envio manual para SAP.'
            submission.sap_sent_at = None
            submission.save(
                update_fields=[
                    'validator_name',
                    'validator_signature_data',
                    'validated_at',
                    'status',
                    'sap_status',
                    'sap_attachment_id',
                    'sap_response_message',
                    'sap_sent_at',
                    'updated_at',
                ]
            )
            return _pdf_download_response(submission)
    else:
        form = ValidationForm()
    return render(request, 'inspecoes/validation.html', {'form': form, 'submission': submission})


@login_required
def form_download_pdf_view(request, pk):
    if not (_can_view(request.user, 'forms') or _can_view(request.user, 'history')):
        return _deny_screen_access(request, 'Detalhe/PDF do formulário')

    submission = get_object_or_404(FormSubmission.objects.select_related('equipment'), pk=pk)
    if submission.status not in [FormSubmission.Status.APPROVED, FormSubmission.Status.SENT_TO_SAP]:
        messages.warning(request, 'Valide o formulário antes de baixar o PDF final.')
        if _can_view(request.user, 'forms') and _can_edit_forms(request.user):
            return redirect('inspecoes:form-validate', pk=submission.pk)
        return redirect('inspecoes:detail', pk=submission.pk)
    return _pdf_download_response(submission)


@login_required
def form_send_sap_view(request, pk):
    if not _can_view(request.user, 'forms'):
        return _deny_screen_access(request, 'Formulários')
    if not _can_edit_forms(request.user):
        return _deny_edit_access(request)

    submission = get_object_or_404(FormSubmission.objects.select_related('equipment'), pk=pk)
    if request.method != 'POST':
        return redirect('inspecoes:detail', pk=submission.pk)

    if submission.status not in [FormSubmission.Status.APPROVED, FormSubmission.Status.SENT_TO_SAP]:
        messages.warning(request, 'Valide o formulário antes de enviar para o SAP.')
        return redirect('inspecoes:form-validate', pk=submission.pk)

    if not submission.acceptance_ok:
        messages.error(request, submission.acceptance_block_reason)
        return redirect('inspecoes:form-validate', pk=submission.pk)

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
def detail_view(request, pk):
    if not (_can_view(request.user, 'forms') or _can_view(request.user, 'history')):
        return _deny_screen_access(request, 'Detalhe do formulário')
    submission = get_object_or_404(FormSubmission.objects.select_related('equipment'), pk=pk)
    return render(request, 'inspecoes/detail.html', {'submission': submission})
