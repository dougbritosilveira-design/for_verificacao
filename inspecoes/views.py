from decimal import Decimal
from pathlib import Path

from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .certificate_parser import parse_flow_certificate, parse_scanner_certificate
from .forms import (
    FlowTechnicalForm,
    LevelTechnicalForm,
    ScannerTechnicalForm,
    SelectionForm,
    TechnicalForm,
    ValidationForm,
)
from .models import Equipment, EquipmentFormCriteria, FormSubmission, PortalNotification, PortalUserAccess
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


def _technician_scoped_equipment_ids(user):
    access = _access_for_user(user)
    if not access or access.role != PortalUserAccess.Role.TECHNICIAN:
        return None
    return access.scoped_equipment_ids


def _visible_equipments_queryset(user):
    queryset = Equipment.objects.all()
    scoped_ids = _technician_scoped_equipment_ids(user)
    if scoped_ids:
        queryset = queryset.filter(pk__in=scoped_ids)
    return queryset


def _can_access_submission_for_equipment_scope(user, submission):
    scoped_ids = _technician_scoped_equipment_ids(user)
    if scoped_ids is None:
        return True
    return submission.equipment_id in scoped_ids


def _resolve_criteria_defaults(equipment, form_type):
    acceptance_value = equipment.acceptance_criterion_pct
    acceptance_unit = equipment.acceptance_criterion_unit or EquipmentFormCriteria.Unit.PERCENT
    uncertainty_unit = equipment.acceptance_criterion_unit or EquipmentFormCriteria.Unit.PERCENT
    if not form_type:
        return acceptance_value, acceptance_unit, uncertainty_unit

    criteria_config = _ensure_equipment_form_criteria(equipment, form_type)
    if criteria_config:
        if criteria_config.acceptance_criterion_value is not None:
            acceptance_value = criteria_config.acceptance_criterion_value
        acceptance_unit = criteria_config.acceptance_criterion_unit or acceptance_unit
        uncertainty_unit = criteria_config.expanded_uncertainty_unit or acceptance_unit
    return acceptance_value, acceptance_unit, uncertainty_unit


def _unpack_criteria_defaults(criteria_defaults):
    """
    Compatibilidade entre versões:
    - formato novo: (acceptance_value, acceptance_unit, uncertainty_unit)
    - formato legado: (acceptance_value, acceptance_unit, uncertainty_value, uncertainty_unit)
    """
    if not isinstance(criteria_defaults, (tuple, list)):
        return Decimal('1.0'), EquipmentFormCriteria.Unit.PERCENT, EquipmentFormCriteria.Unit.PERCENT
    if len(criteria_defaults) >= 4:
        return criteria_defaults[0], criteria_defaults[1], criteria_defaults[3]
    if len(criteria_defaults) >= 3:
        return criteria_defaults[0], criteria_defaults[1], criteria_defaults[2]
    if len(criteria_defaults) == 2:
        return criteria_defaults[0], criteria_defaults[1], EquipmentFormCriteria.Unit.PERCENT
    return Decimal('1.0'), EquipmentFormCriteria.Unit.PERCENT, EquipmentFormCriteria.Unit.PERCENT


def _default_units_for_form(form_type, equipment=None):
    if form_type and (form_type.code or '').strip().upper().startswith(FormSubmission.FORM_CODE_LEVEL):
        return EquipmentFormCriteria.Unit.METER, EquipmentFormCriteria.Unit.METER
    if form_type:
        code = (form_type.code or '').strip().upper()
        title = (form_type.title or '').strip().upper()
        if FormSubmission.FORM_CODE_SCANNER in code or 'SCANNER' in code or 'SCANNER' in title:
            return EquipmentFormCriteria.Unit.MILLIMETER, EquipmentFormCriteria.Unit.MILLIMETER
        if (
            FormSubmission.FORM_CODE_FLOW in code
            or 'VAZAO' in code
            or 'VAZAO' in title
            or 'MEDIDOR DE VAZAO' in title
        ):
            return EquipmentFormCriteria.Unit.PERCENT, EquipmentFormCriteria.Unit.PERCENT
    equipment_unit = (
        equipment.acceptance_criterion_unit
        if equipment and getattr(equipment, 'acceptance_criterion_unit', None)
        else EquipmentFormCriteria.Unit.PERCENT
    )
    return equipment_unit, equipment_unit


def _latest_submission_criteria_values(equipment, form_type):
    latest_submission = (
        FormSubmission.objects.filter(equipment=equipment, form_type=form_type)
        .order_by('-created_at')
        .only('acceptance_criterion_pct')
        .first()
    )
    if not latest_submission:
        return None, None
    return latest_submission.acceptance_criterion_pct, None


def _ensure_equipment_form_criteria(equipment, form_type):
    if not equipment or not form_type:
        return None

    default_acceptance_unit, default_uncertainty_unit = _default_units_for_form(form_type, equipment=equipment)
    latest_acceptance, latest_uncertainty = _latest_submission_criteria_values(equipment, form_type)
    default_acceptance = (
        latest_acceptance
        if latest_acceptance is not None
        else (equipment.acceptance_criterion_pct if equipment.acceptance_criterion_pct is not None else Decimal('1.0'))
    )
    default_uncertainty = None

    criteria_config, _ = EquipmentFormCriteria.objects.get_or_create(
        equipment=equipment,
        form_type=form_type,
        defaults={
            'acceptance_criterion_value': default_acceptance,
            'acceptance_criterion_unit': default_acceptance_unit,
            'expanded_uncertainty_value': default_uncertainty,
            'expanded_uncertainty_unit': default_uncertainty_unit,
        },
    )

    update_fields = []
    if not criteria_config.acceptance_criterion_unit:
        criteria_config.acceptance_criterion_unit = default_acceptance_unit
        update_fields.append('acceptance_criterion_unit')
    if not criteria_config.expanded_uncertainty_unit:
        criteria_config.expanded_uncertainty_unit = default_uncertainty_unit
        update_fields.append('expanded_uncertainty_unit')
    if criteria_config.acceptance_criterion_value is None:
        criteria_config.acceptance_criterion_value = default_acceptance
        update_fields.append('acceptance_criterion_value')
    if criteria_config.expanded_uncertainty_value is None and default_uncertainty is not None:
        criteria_config.expanded_uncertainty_value = default_uncertainty
        update_fields.append('expanded_uncertainty_value')
    if default_acceptance_unit and criteria_config.acceptance_criterion_unit != default_acceptance_unit:
        criteria_config.acceptance_criterion_unit = default_acceptance_unit
        update_fields.append('acceptance_criterion_unit')
    if default_uncertainty_unit and criteria_config.expanded_uncertainty_unit != default_uncertainty_unit:
        criteria_config.expanded_uncertainty_unit = default_uncertainty_unit
        update_fields.append('expanded_uncertainty_unit')

    if update_fields:
        update_fields.append('updated_at')
        criteria_config.save(update_fields=list(dict.fromkeys(update_fields)))
    return criteria_config


def _sync_submission_criteria_from_config(submission):
    if not submission.equipment_id:
        return

    acceptance_value, acceptance_unit, uncertainty_unit = _unpack_criteria_defaults(
        _resolve_criteria_defaults(
            submission.equipment,
            submission.form_type,
        )
    )

    update_fields = []
    if acceptance_value is not None and submission.acceptance_criterion_pct != acceptance_value:
        submission.acceptance_criterion_pct = acceptance_value
        update_fields.append('acceptance_criterion_pct')
    if acceptance_unit and submission.acceptance_criterion_unit != acceptance_unit:
        submission.acceptance_criterion_unit = acceptance_unit
        update_fields.append('acceptance_criterion_unit')
    if submission.expanded_uncertainty_pct is not None:
        submission.expanded_uncertainty_pct = None
        update_fields.append('expanded_uncertainty_pct')
    if uncertainty_unit and submission.expanded_uncertainty_unit != uncertainty_unit:
        submission.expanded_uncertainty_unit = uncertainty_unit
        update_fields.append('expanded_uncertainty_unit')

    if update_fields:
        update_fields.append('updated_at')
        submission.save(update_fields=update_fields)


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


def _is_master_user(user):
    access = _access_for_user(user)
    if not access:
        return user.is_superuser
    return access.is_master_portal


def _can_validate_specific_submission(user, submission):
    if not _can_validate_forms(user):
        return False
    if _is_master_user(user):
        return True
    if submission.assigned_validator_id:
        return submission.assigned_validator_id == user.id
    return True


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


def _deny_equipment_scope_access(request):
    messages.warning(request, 'Seu usuário não possui acesso ao equipamento deste formulário.')
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

    equipment_qs = (
        _visible_equipments_queryset(request.user)
        .filter(active=True)
        .prefetch_related('inspection_form_types')
        .order_by('tag')
    )
    equipment_locations = {str(e.pk): e.location for e in equipment_qs}
    equipment_form_types = {
        str(e.pk): [{'id': str(form.pk), 'label': form.full_label} for form in e.available_form_types]
        for e in equipment_qs
    }

    if request.method == 'POST':
        form = SelectionForm(request.POST, equipment_queryset=equipment_qs)
        if form.is_valid():
            submission = form.save(commit=False)
            submission.created_by = request.user
            acceptance_value, acceptance_unit, uncertainty_unit = _unpack_criteria_defaults(
                _resolve_criteria_defaults(
                    submission.equipment,
                    submission.form_type,
                )
            )
            submission.acceptance_criterion_pct = acceptance_value
            submission.acceptance_criterion_unit = acceptance_unit
            submission.expanded_uncertainty_unit = uncertainty_unit
            if not submission.location_snapshot:
                submission.location_snapshot = submission.equipment.location
            submission.status = FormSubmission.Status.DRAFT
            submission.save()
            messages.success(request, 'Formulário criado. Preencha os dados técnicos.')
            return redirect('inspecoes:form-edit', pk=submission.pk)
    else:
        initial = {}
        equipment_id = request.GET.get('equipment')
        if equipment_id:
            try:
                equipment = equipment_qs.get(pk=equipment_id)
            except Equipment.DoesNotExist:
                equipment = None
            if equipment:
                initial['equipment'] = equipment.pk
                initial['location_snapshot'] = equipment.location
                first_form_type = equipment.available_form_types.first()
                if first_form_type:
                    initial['form_type'] = first_form_type.pk
        form = SelectionForm(initial=initial, equipment_queryset=equipment_qs)
    return render(
        request,
        'inspecoes/selection.html',
        {
            'form': form,
            'equipment_locations': equipment_locations,
            'equipment_form_types': equipment_form_types,
        },
    )


@login_required
def form_edit_view(request, pk):
    if not _can_view(request.user, 'forms'):
        return _deny_screen_access(request, 'Formulários')
    if not _can_edit_forms(request.user):
        return _deny_edit_access(request)

    submission = get_object_or_404(
        FormSubmission.objects.select_related(
            'equipment',
            'created_by',
            'form_type',
            'assigned_validator',
            'assigned_validator__portal_access',
        ),
        pk=pk,
    )
    if not _can_access_submission_for_equipment_scope(request.user, submission):
        return _deny_equipment_scope_access(request)
    if submission.status == FormSubmission.Status.PENDING_VALIDATION:
        messages.warning(request, 'Formulario ja enviado para validacao. Edicao bloqueada.')
        return redirect('inspecoes:detail', pk=submission.pk)
    if submission.status in [FormSubmission.Status.APPROVED, FormSubmission.Status.SENT_TO_SAP]:
        messages.warning(request, 'Formulário já validado. Edição bloqueada.')
        return redirect('inspecoes:detail', pk=submission.pk)

    if submission.status in [FormSubmission.Status.DRAFT, FormSubmission.Status.REWORK_REQUIRED]:
        _sync_submission_criteria_from_config(submission)
        submission.refresh_from_db()

    if submission.is_scanner_form:
        form_class = ScannerTechnicalForm
        template_name = 'inspecoes/form_edit_scanner.html'
    elif submission.is_flow_form:
        form_class = FlowTechnicalForm
        template_name = 'inspecoes/form_edit_flow.html'
    elif submission.is_level_form:
        form_class = LevelTechnicalForm
        template_name = 'inspecoes/form_edit_level.html'
    else:
        form_class = TechnicalForm
        template_name = 'inspecoes/form_edit.html'

    if request.method == 'POST':
        if submission.is_scanner_form or submission.is_flow_form:
            form = form_class(request.POST, request.FILES, instance=submission)
        else:
            form = form_class(request.POST, instance=submission)
        if form.is_valid():
            previous_status = submission.status
            submission = form.save(commit=False)
            selected_validator = form.cleaned_data.get('assigned_validator')
            submission.assigned_validator = selected_validator

            if submission.is_scanner_form and 'parse_certificate' in request.POST:
                submission.save()
                if not submission.scanner_certificate_file:
                    messages.warning(request, 'Anexe o certificado em PDF para fazer a leitura automática.')
                    return redirect('inspecoes:form-edit', pk=submission.pk)

                try:
                    with submission.scanner_certificate_file.open('rb') as certificate_file:
                        parsed = parse_scanner_certificate(
                            certificate_file.read(),
                            filename=Path(submission.scanner_certificate_file.name).name,
                        )
                except Exception as exc:
                    messages.error(request, f'Não foi possível ler o certificado: {exc}')
                    return redirect('inspecoes:form-edit', pk=submission.pk)

                parsed_values = parsed.get('values', {})
                always_update_fields = {
                    'acceptance_criterion_pct',
                    'acceptance_criterion_unit',
                    'scanner_certificate_number',
                    'scanner_provider',
                    'scanner_model',
                    'scanner_serial_number',
                    'scanner_measurement_date',
                    'scanner_release_date',
                    'scanner_u_ref_mm',
                    'scanner_u_rep_mm',
                    'scanner_u_res_mm',
                    'scanner_u_setup_mm',
                    'scanner_u_env_mm',
                    'scanner_k_factor',
                    'scanner_manufacturer_ppm',
                }
                for field_name, value in parsed_values.items():
                    if not hasattr(submission, field_name):
                        continue
                    current_value = getattr(submission, field_name)
                    is_measurement_field = field_name.startswith('scanner_target_') or field_name.startswith('scanner_nominal_') or field_name.startswith('scanner_measured_')
                    should_update = (
                        is_measurement_field
                        or field_name in always_update_fields
                        or current_value in (None, '')
                    )
                    if should_update:
                        setattr(submission, field_name, value)

                submission.acceptance_criterion_unit = EquipmentFormCriteria.Unit.MILLIMETER
                submission.expanded_uncertainty_unit = EquipmentFormCriteria.Unit.MILLIMETER
                submission.save()

                points_found = parsed.get('points_found', 0)
                if points_found:
                    residual_count = parsed.get('residual_count') or 0
                    residual_rep_mm = parsed.get('residual_rep_mm')
                    if residual_count >= 2 and residual_rep_mm is not None:
                        detail_u_rep = f'u_rep calculado pelos resíduos ΔR ({residual_count} pontos): {residual_rep_mm:.3f} mm.'
                    else:
                        detail_u_rep = 'u_rep carregado do valor de precisão do certificado.'
                    messages.success(
                        request,
                        f'Certificado lido com sucesso. {points_found} ponto(s) de medição foram preenchidos automaticamente. {detail_u_rep}',
                    )
                else:
                    messages.warning(
                        request,
                        'Certificado lido, mas não foram encontrados pontos de medição automáticos. '
                        'Preencha os pontos manualmente.',
                    )
                return redirect('inspecoes:form-edit', pk=submission.pk)

            if submission.is_flow_form and 'parse_certificate' in request.POST:
                submission.save()
                if not submission.flow_certificate_file:
                    messages.warning(request, 'Anexe o certificado em PDF para fazer a leitura automática.')
                    return redirect('inspecoes:form-edit', pk=submission.pk)

                try:
                    with submission.flow_certificate_file.open('rb') as certificate_file:
                        parsed = parse_flow_certificate(
                            certificate_file.read(),
                            filename=Path(submission.flow_certificate_file.name).name,
                        )
                except Exception as exc:
                    messages.error(request, f'Não foi possível ler o certificado: {exc}')
                    return redirect('inspecoes:form-edit', pk=submission.pk)

                parsed_values = parsed.get('values', {})
                always_update_fields = {
                    'flow_certificate_number',
                    'flow_provider',
                    'flow_tag_on_certificate',
                    'flow_meter_model',
                    'flow_meter_serial_number',
                    'flow_converter_model',
                    'flow_converter_serial_number',
                    'flow_measurement_date',
                    'flow_release_date',
                    'flow_calibration_range_min_m3h',
                    'flow_calibration_range_max_m3h',
                }
                for field_name, value in parsed_values.items():
                    if not hasattr(submission, field_name):
                        continue
                    current_value = getattr(submission, field_name)
                    is_measurement_field = field_name.startswith('flow_point_label_') or field_name.startswith('flow_calibration_') or field_name.startswith('flow_indicated_') or field_name.startswith('flow_reference_') or field_name.startswith('flow_tendency_') or field_name.startswith('flow_uncertainty_') or field_name.startswith('flow_k_')
                    should_update = (
                        is_measurement_field
                        or field_name in always_update_fields
                        or current_value in (None, '')
                    )
                    if should_update:
                        setattr(submission, field_name, value)

                submission.acceptance_criterion_unit = EquipmentFormCriteria.Unit.PERCENT
                submission.expanded_uncertainty_unit = EquipmentFormCriteria.Unit.PERCENT
                submission.save()

                points_found = parsed.get('points_found', 0)
                if points_found:
                    messages.success(
                        request,
                        f'Certificado lido com sucesso. {points_found} ponto(s) de medição foram preenchidos automaticamente.',
                    )
                else:
                    messages.warning(
                        request,
                        'Certificado lido, mas não foram encontrados pontos de medição automáticos. '
                        'Preencha os pontos manualmente.',
                    )
                return redirect('inspecoes:form-edit', pk=submission.pk)

            if 'go_validate' in request.POST and submission.status in [FormSubmission.Status.DRAFT, FormSubmission.Status.REWORK_REQUIRED]:
                if not selected_validator:
                    form.add_error(
                        'assigned_validator',
                        'Selecione o validador responsável para enviar o formulário.',
                    )
                    return render(request, template_name, {'form': form, 'submission': submission})
                submission.status = FormSubmission.Status.PENDING_VALIDATION
                submission.schedule_validation_deadline()
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
        form = form_class(instance=submission)
    return render(request, template_name, {'form': form, 'submission': submission})


@login_required
def form_validate_view(request, pk):
    if not _can_view(request.user, 'forms'):
        return _deny_screen_access(request, 'Formulários')
    if not _can_validate_forms(request.user):
        return _deny_validate_access(request)

    submission = get_object_or_404(
        FormSubmission.objects.select_related(
            'equipment',
            'created_by',
            'form_type',
            'assigned_validator',
            'assigned_validator__portal_access',
        ),
        pk=pk,
    )
    if not _can_access_submission_for_equipment_scope(request.user, submission):
        return _deny_equipment_scope_access(request)
    if not _can_validate_specific_submission(request.user, submission):
        designated = submission.assigned_validator_label
        messages.warning(
            request,
            f'Este formulário está direcionado para validação por {designated}.',
        )
        if _can_view(request.user, 'history'):
            return redirect('inspecoes:history')
        return redirect('inspecoes:detail', pk=submission.pk)
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
                if _can_view(request.user, 'history'):
                    return redirect('inspecoes:history')
                return redirect('inspecoes:detail', pk=submission.pk)

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
            messages.warning(request, 'Formulário reprovado e devolvido para edição do técnico responsável.')
            if _can_view(request.user, 'history'):
                return redirect('inspecoes:history')
            return redirect('inspecoes:detail', pk=submission.pk)
    else:
        initial_name = request.user.get_full_name().strip() or request.user.username
        form = ValidationForm(initial={'validator_name': initial_name})
    return render(request, 'inspecoes/validation.html', {'form': form, 'submission': submission})


@login_required
def form_download_pdf_view(request, pk):
    if not (_can_view(request.user, 'forms') or _can_view(request.user, 'history')):
        return _deny_screen_access(request, 'Detalhe/PDF do formulário')

    submission = get_object_or_404(FormSubmission.objects.select_related('equipment', 'form_type'), pk=pk)
    if not _can_access_submission_for_equipment_scope(request.user, submission):
        return _deny_equipment_scope_access(request)
    if submission.status not in [FormSubmission.Status.APPROVED, FormSubmission.Status.SENT_TO_SAP]:
        messages.warning(request, 'Valide o formulário antes de baixar o PDF final.')
        if _can_validate_forms(request.user):
            return redirect('inspecoes:form-validate', pk=submission.pk)
        return redirect('inspecoes:detail', pk=submission.pk)
    return _pdf_download_response(submission)


@login_required
def form_download_certificate_view(request, pk):
    if not (_can_view(request.user, 'forms') or _can_view(request.user, 'history')):
        return _deny_screen_access(request, 'Download do certificado')

    submission = get_object_or_404(FormSubmission.objects.select_related('equipment', 'form_type'), pk=pk)
    if not _can_access_submission_for_equipment_scope(request.user, submission):
        return _deny_equipment_scope_access(request)
    if not (submission.is_scanner_form or submission.is_flow_form):
        messages.warning(request, 'Este formulário não possui certificado vinculado.')
        return redirect('inspecoes:detail', pk=submission.pk)

    certificate_file = submission.attached_certificate_file
    if not certificate_file:
        messages.warning(request, 'Nenhum certificado anexado neste formulário.')
        return redirect('inspecoes:detail', pk=submission.pk)

    filename = Path(certificate_file.name).name or f'certificado_formulario_{submission.pk}.pdf'
    response = FileResponse(certificate_file.open('rb'), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@login_required
def form_send_sap_view(request, pk):
    if not _can_view(request.user, 'forms'):
        return _deny_screen_access(request, 'Formulários')
    if not _can_send_sap(request.user):
        return _deny_send_sap_access(request)

    submission = get_object_or_404(FormSubmission.objects.select_related('equipment', 'form_type'), pk=pk)
    if not _can_access_submission_for_equipment_scope(request.user, submission):
        return _deny_equipment_scope_access(request)
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

    qs = FormSubmission.objects.select_related(
        'equipment',
        'form_type',
        'assigned_validator',
        'assigned_validator__portal_access',
    ).all()
    scoped_equipment_ids = _technician_scoped_equipment_ids(request.user)
    if scoped_equipment_ids:
        qs = qs.filter(equipment_id__in=scoped_equipment_ids)
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

    equipments_qs = _visible_equipments_queryset(request.user).order_by('tag')
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
    )
    scoped_equipment_ids = _technician_scoped_equipment_ids(request.user)
    if scoped_equipment_ids:
        notifications = notifications.filter(
            Q(equipment_id__in=scoped_equipment_ids)
            | Q(submission__equipment_id__in=scoped_equipment_ids)
            | (Q(equipment__isnull=True) & Q(submission__isnull=True))
        )
    notifications = notifications[:200]
    return render(
        request,
        'inspecoes/notifications.html',
        {'notifications': notifications},
    )


@login_required
def detail_view(request, pk):
    if not (_can_view(request.user, 'forms') or _can_view(request.user, 'history')):
        return _deny_screen_access(request, 'Detalhe do formulário')
    submission = get_object_or_404(
        FormSubmission.objects.select_related(
            'equipment',
            'created_by',
            'form_type',
            'assigned_validator',
            'assigned_validator__portal_access',
        ),
        pk=pk,
    )
    if not _can_access_submission_for_equipment_scope(request.user, submission):
        return _deny_equipment_scope_access(request)
    return render(request, 'inspecoes/detail.html', {'submission': submission})


