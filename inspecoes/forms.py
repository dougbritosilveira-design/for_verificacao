from decimal import Decimal

from django import forms
from django.contrib.auth import get_user_model
from django.db.models import Q

from .models import Equipment, FormSubmission, InspectionFormType, PortalUserAccess, VolumeStandard

DATE_INPUT_FORMATS = ['%Y-%m-%d', '%d/%m/%Y']


def _validator_users_queryset():
    user_ids = PortalUserAccess.objects.filter(
        role__in=[PortalUserAccess.Role.VALIDATOR, PortalUserAccess.Role.MASTER],
        user__is_active=True,
    ).values_list('user_id', flat=True)
    return (
        get_user_model()
        .objects.filter(pk__in=user_ids, is_active=True)
        .select_related('portal_access')
        .order_by('first_name', 'last_name', 'username')
    )


def _validator_label(user):
    full_name = user.get_full_name().strip() or user.username
    access = getattr(user, 'portal_access', None)
    registration = access.registration_display if access else user.username
    deadline_days = (
        access.validator_deadline_days_effective
        if access
        else PortalUserAccess.default_validator_deadline_days()
    )
    return f'{full_name} ({registration}) - prazo {deadline_days} dia(s)'


def _configure_assigned_validator_field(form_instance):
    field = form_instance.fields.get('assigned_validator')
    if not field:
        return
    field.queryset = _validator_users_queryset()
    field.empty_label = 'Selecione o validador'
    field.label_from_instance = _validator_label
    field.widget.attrs.update(
        {
            'title': 'Selecione o usuário responsável por validar este formulário.',
        }
    )
    assigned_validator_id = getattr(form_instance.instance, 'assigned_validator_id', None)
    if assigned_validator_id and not form_instance.is_bound:
        form_instance.initial.setdefault('assigned_validator', assigned_validator_id)


def _density_static_scales_queryset():
    static_by_description = (
        Q(description__icontains='BALAN')
        & (
            Q(description__icontains='ESTAT')
            | Q(description__icontains='ESTÁT')
        )
    )
    static_by_form_title = (
        Q(inspection_form_types__title__icontains='BALAN')
        & (
            Q(inspection_form_types__title__icontains='ESTATICA')
            | Q(inspection_form_types__title__icontains='ESTÁTICA')
        )
    )
    return (
        Equipment.objects.filter(active=True)
        .filter(
            static_by_description
            | Q(inspection_form_types__code__istartswith='FOR 08.03.005')
            | static_by_form_title
        )
        .distinct()
        .order_by('tag')
    )


def _density_scales_for_transmitter_queryset(density_equipment):
    if not density_equipment:
        return _density_static_scales_queryset()
    linked_scales = density_equipment.density_static_scales.filter(active=True).order_by('tag')
    if linked_scales.exists():
        return linked_scales
    return _density_static_scales_queryset()


class SelectionForm(forms.ModelForm):
    equipment = forms.ModelChoiceField(
        queryset=Equipment.objects.filter(active=True).order_by('tag'),
        label='Equipamento',
        empty_label='Selecione o equipamento',
    )
    form_type = forms.ModelChoiceField(
        queryset=InspectionFormType.objects.none(),
        label='Formulário',
        empty_label='Selecione o formulário',
    )
    density_scale_equipment = forms.ModelChoiceField(
        queryset=_density_static_scales_queryset(),
        required=False,
        label='Balança estática utilizada',
        empty_label='Selecione a balança',
    )
    density_standard_1 = forms.ModelChoiceField(
        queryset=VolumeStandard.objects.filter(active=True).order_by('tag'),
        required=False,
        label='Aferidor 1',
        empty_label='Selecione o aferidor',
    )
    density_standard_2 = forms.ModelChoiceField(
        queryset=VolumeStandard.objects.filter(active=True).order_by('tag'),
        required=False,
        label='Aferidor 2',
        empty_label='Selecione o aferidor',
    )
    density_standard_3 = forms.ModelChoiceField(
        queryset=VolumeStandard.objects.filter(active=True).order_by('tag'),
        required=False,
        label='Aferidor 3',
        empty_label='Selecione o aferidor',
    )

    class Meta:
        model = FormSubmission
        fields = [
            'equipment',
            'form_type',
            'density_scale_equipment',
            'density_standard_1',
            'density_standard_2',
            'density_standard_3',
            'location_snapshot',
            'om_number',
            'execution_date',
            'executor_name',
        ]
        widgets = {'execution_date': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'})}
        labels = {
            'location_snapshot': 'Local',
            'om_number': 'Nº OM',
            'execution_date': 'Data da visita',
            'executor_name': 'Responsável pela verificação',
        }

    def __init__(self, *args, **kwargs):
        equipment_queryset = kwargs.pop('equipment_queryset', None)
        super().__init__(*args, **kwargs)
        if equipment_queryset is None:
            equipment_queryset = Equipment.objects.filter(active=True).order_by('tag')
        self.fields['equipment'].queryset = equipment_queryset
        self.fields['execution_date'].input_formats = DATE_INPUT_FORMATS
        self.fields['execution_date'].localize = False
        self.fields['location_snapshot'].widget.attrs.update(
            {
                'readonly': 'readonly',
                'title': 'Campo preenchido automaticamente conforme o equipamento selecionado.',
                'style': 'background:#f3f0e6;',
            }
        )
        self.fields['density_scale_equipment'].queryset = _density_static_scales_queryset()
        standard_qs = VolumeStandard.objects.filter(active=True).order_by('tag')
        self.fields['density_standard_1'].queryset = standard_qs
        self.fields['density_standard_2'].queryset = standard_qs
        self.fields['density_standard_3'].queryset = standard_qs

        selected_equipment = self._resolve_selected_equipment()
        if selected_equipment:
            allowed_form_types = selected_equipment.available_form_types
            self.fields['form_type'].queryset = allowed_form_types
            self.fields['density_scale_equipment'].queryset = _density_scales_for_transmitter_queryset(selected_equipment)
            if not self.is_bound and not self.initial.get('form_type'):
                first_form_type = allowed_form_types.first()
                if first_form_type:
                    self.initial['form_type'] = first_form_type.pk
        else:
            self.fields['form_type'].queryset = InspectionFormType.objects.none()
            self.fields['form_type'].empty_label = 'Selecione o equipamento primeiro'

    def _resolve_selected_equipment(self):
        equipment_id = None
        if self.is_bound:
            equipment_id = self.data.get('equipment')
        elif self.initial.get('equipment'):
            initial_equipment = self.initial.get('equipment')
            equipment_id = initial_equipment.pk if hasattr(initial_equipment, 'pk') else initial_equipment
        elif self.instance and self.instance.pk and self.instance.equipment_id:
            equipment_id = self.instance.equipment_id

        if not equipment_id:
            return None
        try:
            return self.fields['equipment'].queryset.get(pk=equipment_id)
        except Equipment.DoesNotExist:
            return None

    def clean(self):
        cleaned_data = super().clean()
        equipment = cleaned_data.get('equipment')
        form_type = cleaned_data.get('form_type')
        if equipment:
            cleaned_data['location_snapshot'] = equipment.location
            allowed_form_types = equipment.available_form_types
            if not allowed_form_types.exists():
                self.add_error('equipment', 'Este equipamento não possui formulários cadastrados. Configure no Admin.')
            if form_type and not allowed_form_types.filter(pk=form_type.pk).exists():
                self.add_error('form_type', 'O formulário selecionado não está habilitado para este equipamento.')

        code = (form_type.code or '').strip().upper() if form_type else ''
        title = (form_type.title or '').strip().upper() if form_type else ''
        is_density_form = (
            code.startswith(FormSubmission.FORM_CODE_DENSITY)
            or 'FOR 08.03.003' in code
            or ('DENSIDADE' in title and 'TRANSMISSOR' in title and 'AJUSTE' in title)
        )
        if is_density_form:
            scale = cleaned_data.get('density_scale_equipment')
            std_1 = cleaned_data.get('density_standard_1')
            std_2 = cleaned_data.get('density_standard_2')
            std_3 = cleaned_data.get('density_standard_3')
            linked_scales_qs = equipment.density_static_scales.filter(active=True) if equipment else Equipment.objects.none()
            if equipment and not linked_scales_qs.exists():
                self.add_error(
                    'density_scale_equipment',
                    'Nenhuma balança estática vinculada a este densímetro. Configure no cadastro do equipamento.',
                )
            if not scale:
                self.add_error('density_scale_equipment', 'Selecione a balança estática usada no procedimento.')
            elif linked_scales_qs.exists() and not linked_scales_qs.filter(pk=scale.pk).exists():
                self.add_error(
                    'density_scale_equipment',
                    'Selecione uma balança estática vinculada a este densímetro.',
                )
            if not std_1:
                self.add_error('density_standard_1', 'Selecione o aferidor 1.')
            if not std_2:
                self.add_error('density_standard_2', 'Selecione o aferidor 2.')
            if not std_3:
                self.add_error('density_standard_3', 'Selecione o aferidor 3.')
            standards = [standard.pk for standard in [std_1, std_2, std_3] if standard]
            if len(standards) != len(set(standards)):
                self.add_error('density_standard_3', 'Selecione aferidores diferentes (sem repetição).')
        return cleaned_data


class TechnicalForm(forms.ModelForm):
    assigned_validator = forms.ModelChoiceField(
        queryset=get_user_model().objects.none(),
        required=False,
        label='Validador responsável',
    )
    belt_replaced = forms.TypedChoiceField(
        label='Houve troca de correia?',
        choices=((False, 'Não'), (True, 'Sim')),
        coerce=lambda value: str(value).lower() in {'true', '1', 'sim'},
        empty_value=False,
        widget=forms.Select(),
    )

    class Meta:
        model = FormSubmission
        fields = [
            'om_number',
            'execution_date',
            't1', 't2', 't3',
            'm1', 'm2', 'm3', 'belt_replaced', 'mark_distance',
            'pulses_per_turn_1', 'pulses_per_turn_2', 'pulses_per_turn_3', 'ibm',
            'speed_characteristic_b04',
            'abw_1', 'abw_2', 'abw_3', 'tare_1', 'tare_2', 'tare_3',
            'applied_weight', 'bridge_length', 'belt_length', 'belt_speed_v',
            'il_before_ti', 'il_before_tf', 'il_after_ti', 'il_after_tf',
            'check_weight', 'kor', 'acceptance_criterion_pct', 'expanded_uncertainty_calc_pct',
            'calculated_flow_ic', 'error_before_pct', 'error_after_pct',
            'sector', 'sector_2', 'sector_3',
            'validator_registration',
            'technician_1_name',
            'technician_2_name', 'technician_2_registration',
            'technician_3_name', 'technician_3_registration',
            'standards_used',
            'observation',
        ]
        labels = {
            'om_number': 'Nº OM',
            'execution_date': 'Data da visita',
            't1': 'T1 (s)',
            't2': 'T2 (s)',
            't3': 'T3 (s)',
            'm1': 'M1 (s)',
            'm2': 'M2 (s)',
            'm3': 'M3 (s)',
            'belt_replaced': 'Houve troca de correia?',
            'mark_distance': 'Distância entre marcas (m)',
            'pulses_per_turn_1': 'Pulsos por volta 1 (I/volta)',
            'pulses_per_turn_2': 'Pulsos por volta 2 (I/volta)',
            'pulses_per_turn_3': 'Pulsos por volta 3 (I/volta)',
            'ibm': 'IBM (média de pulsos por volta)',
            'speed_characteristic_b04': 'Característica de velocidade B04 (I/m)',
            'abw_1': 'ABW 1',
            'abw_2': 'ABW 2',
            'abw_3': 'ABW 3',
            'tare_1': 'TARE 1',
            'tare_2': 'TARE 2',
            'tare_3': 'TARE 3',
            'applied_weight': 'Peso aplicado P (kg)',
            'bridge_length': 'Comprimento ponte D (m)',
            'belt_length': 'Comprimento correia L (m)',
            'belt_speed_v': 'Velocidade da correia V (m/s)',
            'il_before_ti': 'IL antes - Ti',
            'il_before_tf': 'IL antes - Tf',
            'il_after_ti': 'IL depois - Ti',
            'il_after_tf': 'IL depois - Tf',
            'check_weight': 'CW (%)',
            'kor': 'KOR (%)',
            'acceptance_criterion_pct': 'Critério de aceitação (%)',
            'expanded_uncertainty_calc_pct': 'Incerteza expandida calculada (%)',
            'calculated_flow_ic': 'Vazão calculada Ic (ton/h)',
            'error_before_pct': 'Erro antes (%)',
            'error_after_pct': 'Erro depois (%)',
            'sector': 'Setor 1',
            'sector_2': 'Setor 2',
            'sector_3': 'Setor 3',
            'validator_registration': 'Matrícula 1',
            'technician_1_name': 'Nome 1',
            'technician_2_name': 'Nome 2',
            'technician_2_registration': 'Matrícula 2',
            'technician_3_name': 'Nome 3',
            'technician_3_registration': 'Matrícula 3',
            'standards_used': 'Padrões utilizados',
            'observation': 'Observação',
        }
        widgets = {
            'execution_date': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}),
            'standards_used': forms.Textarea(attrs={'rows': 2}),
            'observation': forms.Textarea(attrs={'rows': 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _configure_assigned_validator_field(self)
        self.fields['execution_date'].input_formats = DATE_INPUT_FORMATS
        self.fields['execution_date'].localize = False
        if not self.is_bound:
            self.initial['belt_replaced'] = bool(getattr(self.instance, 'belt_replaced', False))
        for field in self.fields.values():
            if isinstance(field, (forms.DecimalField, forms.FloatField, forms.IntegerField)):
                field.widget.attrs.update({'step': '0.001', 'inputmode': 'decimal'})
        self.fields['acceptance_criterion_pct'].widget.attrs.update({'step': '0.1'})
        self.fields['expanded_uncertainty_calc_pct'].widget.attrs.update({'step': '0.01'})

        for name in [
            'ibm',
            'belt_length',
            'speed_characteristic_b04',
            'calculated_flow_ic',
            'error_before_pct',
            'error_after_pct',
            'acceptance_criterion_pct',
            'expanded_uncertainty_calc_pct',
        ]:
            self.fields[name].disabled = True
            self.fields[name].widget.attrs.update(
                {
                    'style': 'background:#f3f0e6;',
                    'title': 'Campo calculado automaticamente pelo sistema.',
                }
            )



class LevelTechnicalForm(forms.ModelForm):
    assigned_validator = forms.ModelChoiceField(
        queryset=get_user_model().objects.none(),
        required=False,
        label='Validador responsável',
    )

    class Meta:
        model = FormSubmission
        fields = [
            'om_number',
            'execution_date',
            'level_before_vm_1', 'level_before_vl_1',
            'level_before_vm_2', 'level_before_vl_2',
            'level_before_vm_3', 'level_before_vl_3',
            'level_before_vm_4', 'level_before_vl_4',
            'level_after_vm_1', 'level_after_vl_1',
            'level_after_vm_2', 'level_after_vl_2',
            'level_after_vm_3', 'level_after_vl_3',
            'level_after_vm_4', 'level_after_vl_4',
            'level_resolution_tape_m',
            'level_resolution_instrument_m',
            'level_coverage_factor_k',
            'acceptance_criterion_pct',
            'expanded_uncertainty_calc_pct',
            'sector', 'sector_2', 'sector_3',
            'validator_registration',
            'technician_1_name',
            'technician_2_name', 'technician_2_registration',
            'technician_3_name', 'technician_3_registration',
            'standards_used',
            'observation',
        ]
        labels = {
            'om_number': 'Nº OM',
            'execution_date': 'Data da visita',
            'level_before_vm_1': 'VM 1 (m)',
            'level_before_vl_1': 'VL 1 (m)',
            'level_before_vm_2': 'VM 2 (m)',
            'level_before_vl_2': 'VL 2 (m)',
            'level_before_vm_3': 'VM 3 (m)',
            'level_before_vl_3': 'VL 3 (m)',
            'level_before_vm_4': 'VM 4 (m)',
            'level_before_vl_4': 'VL 4 (m)',
            'level_after_vm_1': 'VM 1 (m)',
            'level_after_vl_1': 'VL 1 (m)',
            'level_after_vm_2': 'VM 2 (m)',
            'level_after_vl_2': 'VL 2 (m)',
            'level_after_vm_3': 'VM 3 (m)',
            'level_after_vl_3': 'VL 3 (m)',
            'level_after_vm_4': 'VM 4 (m)',
            'level_after_vl_4': 'VL 4 (m)',
            'level_resolution_tape_m': 'Resolução da trena (m)',
            'level_resolution_instrument_m': 'Resolução do transmissor (m)',
            'level_coverage_factor_k': 'Fator de abrangência (k)',
            'acceptance_criterion_pct': 'Critério de aceitação (m)',
            'expanded_uncertainty_calc_pct': 'Incerteza expandida calculada (m)',
            'sector': 'Setor 1',
            'sector_2': 'Setor 2',
            'sector_3': 'Setor 3',
            'validator_registration': 'Matrícula 1',
            'technician_1_name': 'Nome 1',
            'technician_2_name': 'Nome 2',
            'technician_2_registration': 'Matrícula 2',
            'technician_3_name': 'Nome 3',
            'technician_3_registration': 'Matrícula 3',
            'standards_used': 'Padrões utilizados',
            'observation': 'Observação',
        }
        widgets = {
            'execution_date': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}),
            'standards_used': forms.Textarea(attrs={'rows': 2}),
            'observation': forms.Textarea(attrs={'rows': 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _configure_assigned_validator_field(self)
        self.fields['execution_date'].input_formats = DATE_INPUT_FORMATS
        self.fields['execution_date'].localize = False

        for field in self.fields.values():
            if isinstance(field, (forms.DecimalField, forms.FloatField, forms.IntegerField)):
                field.widget.attrs.update({'step': '0.001', 'inputmode': 'decimal'})

        self.fields['acceptance_criterion_pct'].widget.attrs.update({'step': '0.01'})
        self.fields['expanded_uncertainty_calc_pct'].widget.attrs.update({'step': '0.001'})
        self.fields['level_resolution_tape_m'].widget = forms.HiddenInput()
        self.fields['level_resolution_instrument_m'].widget = forms.HiddenInput()
        self.fields['level_coverage_factor_k'].widget = forms.HiddenInput()

        for name in ['acceptance_criterion_pct', 'expanded_uncertainty_calc_pct']:
            self.fields[name].disabled = True
            self.fields[name].widget.attrs.update(
                {
                    'style': 'background:#f3f0e6;',
                    'title': 'Campo calculado/carregado automaticamente pelo sistema.',
                }
            )

        if not self.instance.level_resolution_tape_m:
            self.initial.setdefault('level_resolution_tape_m', Decimal('0.001'))
        if not self.instance.level_resolution_instrument_m:
            self.initial.setdefault('level_resolution_instrument_m', Decimal('0.010'))
        if not self.instance.level_coverage_factor_k:
            self.initial.setdefault('level_coverage_factor_k', Decimal('2.000'))

        criterion_value = self.instance.acceptance_criterion_pct
        if criterion_value is None:
            criterion_value = self.instance.acceptance_limit_pct
        if criterion_value is not None:
            if self.initial.get('acceptance_criterion_pct') in (None, ''):
                self.initial['acceptance_criterion_pct'] = criterion_value

        uncertainty_calc = self.instance.expanded_uncertainty_calc_value
        if uncertainty_calc is not None:
            if self.initial.get('expanded_uncertainty_calc_pct') in (None, ''):
                self.initial['expanded_uncertainty_calc_pct'] = uncertainty_calc


class ScannerTechnicalForm(forms.ModelForm):
    assigned_validator = forms.ModelChoiceField(
        queryset=get_user_model().objects.none(),
        required=False,
        label='Validador responsável',
    )

    class Meta:
        model = FormSubmission
        fields = [
            'om_number',
            'execution_date',
            'scanner_certificate_file',
            'scanner_certificate_number',
            'scanner_provider',
            'scanner_model',
            'scanner_serial_number',
            'scanner_measurement_date',
            'scanner_release_date',
            'acceptance_criterion_pct',
            'expanded_uncertainty_calc_pct',
            'scanner_manufacturer_ppm',
            'scanner_k_factor',
            'scanner_u_ref_mm',
            'scanner_u_rep_mm',
            'scanner_u_res_mm',
            'scanner_u_setup_mm',
            'scanner_u_env_mm',
            'scanner_target_1', 'scanner_nominal_1_m', 'scanner_measured_1_m',
            'scanner_target_2', 'scanner_nominal_2_m', 'scanner_measured_2_m',
            'scanner_target_3', 'scanner_nominal_3_m', 'scanner_measured_3_m',
            'scanner_target_4', 'scanner_nominal_4_m', 'scanner_measured_4_m',
            'scanner_target_5', 'scanner_nominal_5_m', 'scanner_measured_5_m',
            'scanner_target_6', 'scanner_nominal_6_m', 'scanner_measured_6_m',
            'sector', 'sector_2', 'sector_3',
            'validator_registration',
            'technician_1_name',
            'technician_2_name', 'technician_2_registration',
            'technician_3_name', 'technician_3_registration',
            'observation',
        ]
        labels = {
            'om_number': 'Nº OM',
            'execution_date': 'Data da visita',
            'scanner_certificate_file': 'Certificado de calibração (PDF)',
            'scanner_certificate_number': 'Número do certificado',
            'scanner_provider': 'Laboratório / fornecedor',
            'scanner_model': 'Modelo do scanner',
            'scanner_serial_number': 'Número de série',
            'scanner_measurement_date': 'Data da medição no certificado',
            'scanner_release_date': 'Data de emissão do certificado',
            'acceptance_criterion_pct': 'Critério de aceitação fixo (mm)',
            'expanded_uncertainty_calc_pct': 'Incerteza expandida calculada U(e) (mm)',
            'scanner_manufacturer_ppm': 'Parcela do fabricante (ppm)',
            'scanner_k_factor': 'Fator de abrangência (k)',
            'scanner_u_ref_mm': 'u_ref (mm)',
            'scanner_u_rep_mm': 'u_rep (mm)',
            'scanner_u_res_mm': 'u_res (mm)',
            'scanner_u_setup_mm': 'u_setup (mm)',
            'scanner_u_env_mm': 'u_env (mm)',
            'scanner_target_1': 'Ponto 1',
            'scanner_nominal_1_m': 'Nominal 1 (m)',
            'scanner_measured_1_m': 'Medido 1 (m)',
            'scanner_target_2': 'Ponto 2',
            'scanner_nominal_2_m': 'Nominal 2 (m)',
            'scanner_measured_2_m': 'Medido 2 (m)',
            'scanner_target_3': 'Ponto 3',
            'scanner_nominal_3_m': 'Nominal 3 (m)',
            'scanner_measured_3_m': 'Medido 3 (m)',
            'scanner_target_4': 'Ponto 4',
            'scanner_nominal_4_m': 'Nominal 4 (m)',
            'scanner_measured_4_m': 'Medido 4 (m)',
            'scanner_target_5': 'Ponto 5',
            'scanner_nominal_5_m': 'Nominal 5 (m)',
            'scanner_measured_5_m': 'Medido 5 (m)',
            'scanner_target_6': 'Ponto 6',
            'scanner_nominal_6_m': 'Nominal 6 (m)',
            'scanner_measured_6_m': 'Medido 6 (m)',
            'sector': 'Setor 1',
            'sector_2': 'Setor 2',
            'sector_3': 'Setor 3',
            'validator_registration': 'Matrícula 1',
            'technician_1_name': 'Nome 1',
            'technician_2_name': 'Nome 2',
            'technician_2_registration': 'Matrícula 2',
            'technician_3_name': 'Nome 3',
            'technician_3_registration': 'Matrícula 3',
            'observation': 'Observação',
        }
        widgets = {
            'execution_date': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}),
            'scanner_measurement_date': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}),
            'scanner_release_date': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}),
            'observation': forms.Textarea(attrs={'rows': 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _configure_assigned_validator_field(self)
        for date_field in ['execution_date', 'scanner_measurement_date', 'scanner_release_date']:
            self.fields[date_field].input_formats = DATE_INPUT_FORMATS
            self.fields[date_field].localize = False

        for field in self.fields.values():
            if isinstance(field, (forms.DecimalField, forms.FloatField, forms.IntegerField)):
                field.widget.attrs.update({'step': '0.001', 'inputmode': 'decimal'})

        self.fields['acceptance_criterion_pct'].widget.attrs.update({'step': '0.01'})
        self.fields['expanded_uncertainty_calc_pct'].widget.attrs.update({'step': '0.001'})
        self.fields['scanner_manufacturer_ppm'].widget.attrs.update({'step': '0.1'})
        self.fields['scanner_k_factor'].widget.attrs.update({'step': '0.1'})

        for name in [
            'acceptance_criterion_pct',
            'expanded_uncertainty_calc_pct',
            'scanner_u_ref_mm',
            'scanner_u_rep_mm',
            'scanner_u_res_mm',
            'scanner_u_setup_mm',
            'scanner_u_env_mm',
            'scanner_k_factor',
            'scanner_manufacturer_ppm',
        ]:
            self.fields[name].disabled = True
            self.fields[name].widget.attrs.update(
                {
                    'style': 'background:#f3f0e6;',
                    'title': 'Campo carregado/calculado automaticamente pelo sistema.',
                }
            )

        if not self.instance.scanner_manufacturer_ppm:
            self.initial.setdefault('scanner_manufacturer_ppm', Decimal('10.0'))
        if not self.instance.scanner_k_factor:
            self.initial.setdefault('scanner_k_factor', Decimal('2.0'))
        if self.instance.scanner_u_ref_mm is None:
            self.initial.setdefault('scanner_u_ref_mm', Decimal('0.000'))
        if self.instance.scanner_u_res_mm is None:
            self.initial.setdefault('scanner_u_res_mm', Decimal('0.000'))
        if self.instance.scanner_u_setup_mm is None:
            self.initial.setdefault('scanner_u_setup_mm', Decimal('0.000'))
        if self.instance.scanner_u_env_mm is None:
            self.initial.setdefault('scanner_u_env_mm', Decimal('0.000'))


class FlowTechnicalForm(forms.ModelForm):
    assigned_validator = forms.ModelChoiceField(
        queryset=get_user_model().objects.none(),
        required=False,
        label='Validador responsável',
    )

    class Meta:
        model = FormSubmission
        fields = [
            'om_number',
            'execution_date',
            'flow_certificate_file',
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
            'acceptance_criterion_pct',
            'flow_point_label_1', 'flow_calibration_1_m3h', 'flow_indicated_1_m3h', 'flow_reference_1_m3h', 'flow_tendency_1_pct', 'flow_uncertainty_1_pct', 'flow_k_1',
            'flow_point_label_2', 'flow_calibration_2_m3h', 'flow_indicated_2_m3h', 'flow_reference_2_m3h', 'flow_tendency_2_pct', 'flow_uncertainty_2_pct', 'flow_k_2',
            'flow_point_label_3', 'flow_calibration_3_m3h', 'flow_indicated_3_m3h', 'flow_reference_3_m3h', 'flow_tendency_3_pct', 'flow_uncertainty_3_pct', 'flow_k_3',
            'flow_point_label_4', 'flow_calibration_4_m3h', 'flow_indicated_4_m3h', 'flow_reference_4_m3h', 'flow_tendency_4_pct', 'flow_uncertainty_4_pct', 'flow_k_4',
            'flow_point_label_5', 'flow_calibration_5_m3h', 'flow_indicated_5_m3h', 'flow_reference_5_m3h', 'flow_tendency_5_pct', 'flow_uncertainty_5_pct', 'flow_k_5',
            'flow_point_label_6', 'flow_calibration_6_m3h', 'flow_indicated_6_m3h', 'flow_reference_6_m3h', 'flow_tendency_6_pct', 'flow_uncertainty_6_pct', 'flow_k_6',
            'sector', 'sector_2', 'sector_3',
            'validator_registration',
            'technician_1_name',
            'technician_2_name', 'technician_2_registration',
            'technician_3_name', 'technician_3_registration',
            'standards_used',
            'observation',
        ]
        labels = {
            'om_number': 'Nº OM',
            'execution_date': 'Data da visita',
            'flow_certificate_file': 'Certificado de calibração (PDF)',
            'flow_certificate_number': 'Número do certificado',
            'flow_provider': 'Laboratório / fornecedor',
            'flow_tag_on_certificate': 'TAG no certificado',
            'flow_meter_model': 'Modelo do medidor',
            'flow_meter_serial_number': 'Série do medidor',
            'flow_converter_model': 'Modelo do conversor',
            'flow_converter_serial_number': 'Série do conversor',
            'flow_measurement_date': 'Data da calibração',
            'flow_release_date': 'Data de emissão do certificado',
            'flow_calibration_range_min_m3h': 'Faixa calibrada mínima (m³/h)',
            'flow_calibration_range_max_m3h': 'Faixa calibrada máxima (m³/h)',
            'acceptance_criterion_pct': 'Critério de aceitação (%)',
            'flow_point_label_1': 'Ponto 1',
            'flow_calibration_1_m3h': 'Vazão de calibração 1 (m³/h)',
            'flow_indicated_1_m3h': 'Valor indicado 1 (m³/h)',
            'flow_reference_1_m3h': 'Valor de referência 1 (m³/h)',
            'flow_tendency_1_pct': 'Tendência 1 (%)',
            'flow_uncertainty_1_pct': 'U(e) 1 (%)',
            'flow_k_1': 'k 1',
            'flow_point_label_2': 'Ponto 2',
            'flow_calibration_2_m3h': 'Vazão de calibração 2 (m³/h)',
            'flow_indicated_2_m3h': 'Valor indicado 2 (m³/h)',
            'flow_reference_2_m3h': 'Valor de referência 2 (m³/h)',
            'flow_tendency_2_pct': 'Tendência 2 (%)',
            'flow_uncertainty_2_pct': 'U(e) 2 (%)',
            'flow_k_2': 'k 2',
            'flow_point_label_3': 'Ponto 3',
            'flow_calibration_3_m3h': 'Vazão de calibração 3 (m³/h)',
            'flow_indicated_3_m3h': 'Valor indicado 3 (m³/h)',
            'flow_reference_3_m3h': 'Valor de referência 3 (m³/h)',
            'flow_tendency_3_pct': 'Tendência 3 (%)',
            'flow_uncertainty_3_pct': 'U(e) 3 (%)',
            'flow_k_3': 'k 3',
            'flow_point_label_4': 'Ponto 4',
            'flow_calibration_4_m3h': 'Vazão de calibração 4 (m³/h)',
            'flow_indicated_4_m3h': 'Valor indicado 4 (m³/h)',
            'flow_reference_4_m3h': 'Valor de referência 4 (m³/h)',
            'flow_tendency_4_pct': 'Tendência 4 (%)',
            'flow_uncertainty_4_pct': 'U(e) 4 (%)',
            'flow_k_4': 'k 4',
            'flow_point_label_5': 'Ponto 5',
            'flow_calibration_5_m3h': 'Vazão de calibração 5 (m³/h)',
            'flow_indicated_5_m3h': 'Valor indicado 5 (m³/h)',
            'flow_reference_5_m3h': 'Valor de referência 5 (m³/h)',
            'flow_tendency_5_pct': 'Tendência 5 (%)',
            'flow_uncertainty_5_pct': 'U(e) 5 (%)',
            'flow_k_5': 'k 5',
            'flow_point_label_6': 'Ponto 6',
            'flow_calibration_6_m3h': 'Vazão de calibração 6 (m³/h)',
            'flow_indicated_6_m3h': 'Valor indicado 6 (m³/h)',
            'flow_reference_6_m3h': 'Valor de referência 6 (m³/h)',
            'flow_tendency_6_pct': 'Tendência 6 (%)',
            'flow_uncertainty_6_pct': 'U(e) 6 (%)',
            'flow_k_6': 'k 6',
            'sector': 'Setor 1',
            'sector_2': 'Setor 2',
            'sector_3': 'Setor 3',
            'validator_registration': 'Matrícula 1',
            'technician_1_name': 'Nome 1',
            'technician_2_name': 'Nome 2',
            'technician_2_registration': 'Matrícula 2',
            'technician_3_name': 'Nome 3',
            'technician_3_registration': 'Matrícula 3',
            'standards_used': 'Padrões utilizados',
            'observation': 'Observação',
        }
        widgets = {
            'execution_date': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}),
            'flow_measurement_date': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}),
            'flow_release_date': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}),
            'standards_used': forms.Textarea(attrs={'rows': 2}),
            'observation': forms.Textarea(attrs={'rows': 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _configure_assigned_validator_field(self)
        for date_field in ['execution_date', 'flow_measurement_date', 'flow_release_date']:
            self.fields[date_field].input_formats = DATE_INPUT_FORMATS
            self.fields[date_field].localize = False

        for field in self.fields.values():
            if isinstance(field, (forms.DecimalField, forms.FloatField, forms.IntegerField)):
                field.widget.attrs.update({'step': '0.001', 'inputmode': 'decimal'})

        self.fields['acceptance_criterion_pct'].widget.attrs.update({'step': '0.1'})

        for name in ['acceptance_criterion_pct']:
            self.fields[name].disabled = True
            self.fields[name].widget.attrs.update(
                {
                    'style': 'background:#f3f0e6;',
                    'title': 'Campo carregado/calculado automaticamente pelo sistema.',
                }
            )


class FlowAdjustTechnicalForm(forms.ModelForm):
    assigned_validator = forms.ModelChoiceField(
        queryset=get_user_model().objects.none(),
        required=False,
        label='Validador responsável',
    )

    class Meta:
        model = FormSubmission
        fields = [
            'om_number',
            'execution_date',
            'flow_adjust_thickness_1_mm',
            'flow_adjust_thickness_2_mm',
            'flow_adjust_thickness_3_mm',
            'flow_adjust_thickness_4_mm',
            'flow_adjust_circumference_ci_mm',
            'flow_adjust_pipe_nominal_in',
            'flow_adjust_before_totmv_m3',
            'flow_adjust_before_totsup_m3',
            'flow_adjust_before_totmv_start_time',
            'flow_adjust_before_totmv_end_time',
            'flow_adjust_before_totsup_start_time',
            'flow_adjust_before_totsup_end_time',
            'flow_adjust_after_totmv_m3',
            'flow_adjust_after_totsup_m3',
            'flow_adjust_after_totmv_start_time',
            'flow_adjust_after_totmv_end_time',
            'flow_adjust_after_totsup_start_time',
            'flow_adjust_after_totsup_end_time',
            'flow_adjust_u_ci_mm',
            'flow_adjust_u_inst_t_mm',
            'flow_adjust_u_delta_t_s',
            'flow_adjust_u_dut_repeat_pct',
            'flow_adjust_u_dut_res_pct',
            'flow_adjust_k_factor',
            'acceptance_criterion_pct',
            'expanded_uncertainty_calc_pct',
            'error_before_pct',
            'error_after_pct',
            'sector',
            'sector_2',
            'sector_3',
            'validator_registration',
            'technician_1_name',
            'technician_2_name',
            'technician_2_registration',
            'technician_3_name',
            'technician_3_registration',
            'standards_used',
            'observation',
        ]
        labels = {
            'om_number': 'Nº OM',
            'execution_date': 'Data da visita',
            'flow_adjust_thickness_1_mm': 'Espessura 1 (mm)',
            'flow_adjust_thickness_2_mm': 'Espessura 2 (mm)',
            'flow_adjust_thickness_3_mm': 'Espessura 3 (mm)',
            'flow_adjust_thickness_4_mm': 'Espessura 4 (mm)',
            'flow_adjust_circumference_ci_mm': 'Circunferência CI (mm)',
            'flow_adjust_pipe_nominal_in': "POL' da tubulação (pol)",
            'flow_adjust_before_totmv_m3': 'TOTMV antes (m³)',
            'flow_adjust_before_totsup_m3': 'TOTSUP antes (m³)',
            'flow_adjust_before_totmv_start_time': 'Hora início TOTMV',
            'flow_adjust_before_totmv_end_time': 'Hora final TOTMV',
            'flow_adjust_before_totsup_start_time': 'Hora início TOTSUP',
            'flow_adjust_before_totsup_end_time': 'Hora final TOTSUP',
            'flow_adjust_after_totmv_m3': 'TOTMV após ajuste (m³)',
            'flow_adjust_after_totsup_m3': 'TOTSUP após ajuste (m³)',
            'flow_adjust_after_totmv_start_time': 'Hora início TOTMV',
            'flow_adjust_after_totmv_end_time': 'Hora final TOTMV',
            'flow_adjust_after_totsup_start_time': 'Hora início TOTSUP',
            'flow_adjust_after_totsup_end_time': 'Hora final TOTSUP',
            'flow_adjust_u_ci_mm': 'u(CI) (mm, 1σ)',
            'flow_adjust_u_inst_t_mm': 'u_inst_t (mm, 1σ)',
            'flow_adjust_u_delta_t_s': 'u(Δt) (s, 1σ)',
            'flow_adjust_u_dut_repeat_pct': 'u_repeat DUT (%, 1σ)',
            'flow_adjust_u_dut_res_pct': 'u_res DUT (%, 1σ)',
            'flow_adjust_k_factor': 'Fator k',
            'acceptance_criterion_pct': 'Critério de aceitação (%)',
            'expanded_uncertainty_calc_pct': 'Incerteza expandida calculada U(e) (%)',
            'error_before_pct': 'Erro antes (%)',
            'error_after_pct': 'Erro final (%)',
            'sector': 'Setor 1',
            'sector_2': 'Setor 2',
            'sector_3': 'Setor 3',
            'validator_registration': 'Matrícula 1',
            'technician_1_name': 'Nome 1',
            'technician_2_name': 'Nome 2',
            'technician_2_registration': 'Matrícula 2',
            'technician_3_name': 'Nome 3',
            'technician_3_registration': 'Matrícula 3',
            'standards_used': 'Padrões utilizados',
            'observation': 'Observação',
        }
        widgets = {
            'execution_date': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}),
            'flow_adjust_before_totmv_start_time': forms.TimeInput(format='%H:%M', attrs={'type': 'time'}),
            'flow_adjust_before_totmv_end_time': forms.TimeInput(format='%H:%M', attrs={'type': 'time'}),
            'flow_adjust_before_totsup_start_time': forms.TimeInput(format='%H:%M', attrs={'type': 'time'}),
            'flow_adjust_before_totsup_end_time': forms.TimeInput(format='%H:%M', attrs={'type': 'time'}),
            'flow_adjust_after_totmv_start_time': forms.TimeInput(format='%H:%M', attrs={'type': 'time'}),
            'flow_adjust_after_totmv_end_time': forms.TimeInput(format='%H:%M', attrs={'type': 'time'}),
            'flow_adjust_after_totsup_start_time': forms.TimeInput(format='%H:%M', attrs={'type': 'time'}),
            'flow_adjust_after_totsup_end_time': forms.TimeInput(format='%H:%M', attrs={'type': 'time'}),
            'standards_used': forms.Textarea(attrs={'rows': 2}),
            'observation': forms.Textarea(attrs={'rows': 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _configure_assigned_validator_field(self)
        self.fields['execution_date'].input_formats = DATE_INPUT_FORMATS
        self.fields['execution_date'].localize = False

        for field in self.fields.values():
            if isinstance(field, (forms.DecimalField, forms.FloatField, forms.IntegerField)):
                field.widget.attrs.update({'step': '0.001', 'inputmode': 'decimal'})

        self.fields['acceptance_criterion_pct'].widget.attrs.update({'step': '0.1'})
        self.fields['expanded_uncertainty_calc_pct'].widget.attrs.update({'step': '0.01'})
        self.fields['error_before_pct'].widget.attrs.update({'step': '0.01'})
        self.fields['error_after_pct'].widget.attrs.update({'step': '0.01'})

        # Mantemos os parâmetros para cálculo interno, mas ocultos na interface.
        for name in [
            'flow_adjust_u_ci_mm',
            'flow_adjust_u_inst_t_mm',
            'flow_adjust_u_delta_t_s',
            'flow_adjust_u_dut_repeat_pct',
            'flow_adjust_u_dut_res_pct',
        ]:
            self.fields[name].widget = forms.HiddenInput()
            self.fields[name].required = False

        # Fator k deve ficar visível apenas em modo leitura.
        self.fields['flow_adjust_k_factor'].disabled = True
        self.fields['flow_adjust_k_factor'].widget.attrs.update(
            {
                'style': 'background:#f3f0e6;',
                'title': 'Campo bloqueado. Valor padrão do método.',
            }
        )

        for name in [
            'acceptance_criterion_pct',
            'expanded_uncertainty_calc_pct',
            'error_before_pct',
            'error_after_pct',
        ]:
            self.fields[name].disabled = True
            self.fields[name].widget.attrs.update(
                {
                    'style': 'background:#f3f0e6;',
                    'title': 'Campo calculado automaticamente pelo sistema.',
                }
            )

        if self.instance.flow_adjust_u_ci_mm is None:
            self.initial.setdefault('flow_adjust_u_ci_mm', Decimal('1.000'))
        if self.instance.flow_adjust_u_inst_t_mm is None:
            self.initial.setdefault('flow_adjust_u_inst_t_mm', Decimal('0.200'))
        if self.instance.flow_adjust_u_delta_t_s is None:
            self.initial.setdefault('flow_adjust_u_delta_t_s', Decimal('5.000'))
        if self.instance.flow_adjust_u_dut_repeat_pct is None:
            self.initial.setdefault('flow_adjust_u_dut_repeat_pct', Decimal('0.000'))
        if self.instance.flow_adjust_u_dut_res_pct is None:
            self.initial.setdefault('flow_adjust_u_dut_res_pct', Decimal('0.000'))
        if self.instance.flow_adjust_k_factor is None:
            self.initial.setdefault('flow_adjust_k_factor', Decimal('2.000'))


class DensityTechnicalForm(forms.ModelForm):
    assigned_validator = forms.ModelChoiceField(
        queryset=get_user_model().objects.none(),
        required=False,
        label='Validador responsável',
    )

    class Meta:
        model = FormSubmission
        fields = [
            'om_number',
            'execution_date',
            'density_scale_equipment',
            'density_standard_1',
            'density_standard_2',
            'density_standard_3',
            'density_scale_mab_kg',
            'density_scale_mib_kg',
            'density_scale_criterion_pct',
            'density_scale_u_additional_kg',
            'density_before_low_point_gcm3',
            'density_before_high_point_gcm3',
            'density_before_low_count_cts',
            'density_before_high_count_cts',
            'density_before_empty_1_kg',
            'density_before_full_1_kg',
            'density_before_volume_1_l',
            'density_before_empty_2_kg',
            'density_before_full_2_kg',
            'density_before_volume_2_l',
            'density_before_empty_3_kg',
            'density_before_full_3_kg',
            'density_before_volume_3_l',
            'density_before_mds_informed_gcm3',
            'density_before_mds_reading_1_gcm3',
            'density_before_mds_reading_2_gcm3',
            'density_before_mds_reading_3_gcm3',
            'density_before_mds_reading_4_gcm3',
            'density_before_mds_reading_5_gcm3',
            'density_after_low_point_gcm3',
            'density_after_high_point_gcm3',
            'density_after_low_count_cts',
            'density_after_high_count_cts',
            'density_after_empty_1_kg',
            'density_after_full_1_kg',
            'density_after_volume_1_l',
            'density_after_empty_2_kg',
            'density_after_full_2_kg',
            'density_after_volume_2_l',
            'density_after_empty_3_kg',
            'density_after_full_3_kg',
            'density_after_volume_3_l',
            'density_after_mds_informed_gcm3',
            'density_after_mds_reading_1_gcm3',
            'density_after_mds_reading_2_gcm3',
            'density_after_mds_reading_3_gcm3',
            'density_after_mds_reading_4_gcm3',
            'density_after_mds_reading_5_gcm3',
            'density_volume_graduation_l',
            'density_mds_resolution_gcm3',
            'density_k_factor',
            'acceptance_criterion_pct',
            'expanded_uncertainty_calc_pct',
            'error_before_pct',
            'error_after_pct',
            'sector',
            'sector_2',
            'sector_3',
            'validator_registration',
            'technician_1_name',
            'technician_2_name',
            'technician_2_registration',
            'technician_3_name',
            'technician_3_registration',
            'standards_used',
            'observation',
        ]
        labels = {
            'om_number': 'Nº OM',
            'execution_date': 'Data da visita',
            'density_scale_equipment': 'Balança estática utilizada',
            'density_standard_1': 'Aferidor 1',
            'density_standard_2': 'Aferidor 2',
            'density_standard_3': 'Aferidor 3',
            'density_scale_mab_kg': 'MAB - Massa aplicada na balança (kg)',
            'density_scale_mib_kg': 'MIB - Massa indicada pela balança (kg)',
            'density_scale_criterion_pct': 'Critério da balança (%)',
            'density_scale_u_additional_kg': 'u adicional por pesagem (kg)',
            'density_before_low_point_gcm3': 'Densidade ponto baixo (g/cm³)',
            'density_before_high_point_gcm3': 'Densidade ponto alto (g/cm³)',
            'density_before_low_count_cts': 'Contagens ponto baixo (ct/s)',
            'density_before_high_count_cts': 'Contagens ponto alto (ct/s)',
            'density_before_empty_1_kg': 'AV1 vazio (kg)',
            'density_before_full_1_kg': 'AV1 cheio (kg)',
            'density_before_volume_1_l': 'AV1 volume (L)',
            'density_before_empty_2_kg': 'AV2 vazio (kg)',
            'density_before_full_2_kg': 'AV2 cheio (kg)',
            'density_before_volume_2_l': 'AV2 volume (L)',
            'density_before_empty_3_kg': 'AV3 vazio (kg)',
            'density_before_full_3_kg': 'AV3 cheio (kg)',
            'density_before_volume_3_l': 'AV3 volume (L)',
            'density_before_mds_informed_gcm3': 'MDS informado antes (g/cm³)',
            'density_before_mds_reading_1_gcm3': 'Leitura MDS antes 1',
            'density_before_mds_reading_2_gcm3': 'Leitura MDS antes 2',
            'density_before_mds_reading_3_gcm3': 'Leitura MDS antes 3',
            'density_before_mds_reading_4_gcm3': 'Leitura MDS antes 4',
            'density_before_mds_reading_5_gcm3': 'Leitura MDS antes 5',
            'density_after_low_point_gcm3': 'Densidade ponto baixo (g/cm³)',
            'density_after_high_point_gcm3': 'Densidade ponto alto (g/cm³)',
            'density_after_low_count_cts': 'Contagens ponto baixo (ct/s)',
            'density_after_high_count_cts': 'Contagens ponto alto (ct/s)',
            'density_after_empty_1_kg': 'AV1 vazio (kg)',
            'density_after_full_1_kg': 'AV1 cheio (kg)',
            'density_after_volume_1_l': 'AV1 volume (L)',
            'density_after_empty_2_kg': 'AV2 vazio (kg)',
            'density_after_full_2_kg': 'AV2 cheio (kg)',
            'density_after_volume_2_l': 'AV2 volume (L)',
            'density_after_empty_3_kg': 'AV3 vazio (kg)',
            'density_after_full_3_kg': 'AV3 cheio (kg)',
            'density_after_volume_3_l': 'AV3 volume (L)',
            'density_after_mds_informed_gcm3': 'MDS informado após (g/cm³)',
            'density_after_mds_reading_1_gcm3': 'Leitura MDS após 1',
            'density_after_mds_reading_2_gcm3': 'Leitura MDS após 2',
            'density_after_mds_reading_3_gcm3': 'Leitura MDS após 3',
            'density_after_mds_reading_4_gcm3': 'Leitura MDS após 4',
            'density_after_mds_reading_5_gcm3': 'Leitura MDS após 5',
            'density_volume_graduation_l': 'Graduação do aferidor (L)',
            'density_mds_resolution_gcm3': 'Resolução do MDS (g/cm³)',
            'density_k_factor': 'Fator k',
            'acceptance_criterion_pct': 'Critério de aceitação (%)',
            'expanded_uncertainty_calc_pct': 'Incerteza expandida calculada U(e) (%)',
            'error_before_pct': 'Erro antes (%)',
            'error_after_pct': 'Erro final (%)',
            'sector': 'Setor 1',
            'sector_2': 'Setor 2',
            'sector_3': 'Setor 3',
            'validator_registration': 'Matrícula 1',
            'technician_1_name': 'Nome 1',
            'technician_2_name': 'Nome 2',
            'technician_2_registration': 'Matrícula 2',
            'technician_3_name': 'Nome 3',
            'technician_3_registration': 'Matrícula 3',
            'standards_used': 'Padrões utilizados',
            'observation': 'Observação',
        }
        widgets = {
            'execution_date': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}),
            'standards_used': forms.Textarea(attrs={'rows': 2}),
            'observation': forms.Textarea(attrs={'rows': 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _configure_assigned_validator_field(self)
        self.fields['execution_date'].input_formats = DATE_INPUT_FORMATS
        self.fields['execution_date'].localize = False

        scale_queryset = _density_scales_for_transmitter_queryset(self.instance.equipment)
        if self.instance and self.instance.density_scale_equipment_id:
            scale_queryset = (
                scale_queryset
                | Equipment.objects.filter(pk=self.instance.density_scale_equipment_id)
            ).distinct().order_by('tag')
        self.fields['density_scale_equipment'].queryset = scale_queryset
        standard_qs = VolumeStandard.objects.filter(active=True).order_by('tag')
        self.fields['density_standard_1'].queryset = standard_qs
        self.fields['density_standard_2'].queryset = standard_qs
        self.fields['density_standard_3'].queryset = standard_qs

        for field in self.fields.values():
            if isinstance(field, (forms.DecimalField, forms.FloatField, forms.IntegerField)):
                field.widget.attrs.update({'step': '0.001', 'inputmode': 'decimal'})

        self.fields['acceptance_criterion_pct'].widget.attrs.update({'step': '0.1'})
        self.fields['expanded_uncertainty_calc_pct'].widget.attrs.update({'step': '0.01'})
        self.fields['error_before_pct'].widget.attrs.update({'step': '0.01'})
        self.fields['error_after_pct'].widget.attrs.update({'step': '0.01'})
        self.fields['density_scale_criterion_pct'].widget.attrs.update({'step': '0.1'})
        self.fields['density_scale_u_additional_kg'].widget.attrs.update({'step': '0.0001'})
        self.fields['density_volume_graduation_l'].widget.attrs.update({'step': '0.0001'})
        self.fields['density_mds_resolution_gcm3'].widget.attrs.update({'step': '0.0001'})

        self.fields['density_scale_criterion_pct'].disabled = True
        self.fields['density_scale_criterion_pct'].widget.attrs.update(
            {
                'style': 'background:#f3f0e6;',
                'title': 'Campo padrão do procedimento.',
            }
        )
        for name in ['acceptance_criterion_pct', 'expanded_uncertainty_calc_pct', 'error_before_pct', 'error_after_pct']:
            self.fields[name].disabled = True
            self.fields[name].widget.attrs.update(
                {
                    'style': 'background:#f3f0e6;',
                    'title': 'Campo calculado automaticamente pelo sistema.',
                }
            )

        if self.instance.density_scale_criterion_pct is None:
            self.initial.setdefault('density_scale_criterion_pct', Decimal('1.000'))
        if self.instance.density_scale_u_additional_kg is None:
            self.initial.setdefault('density_scale_u_additional_kg', Decimal('0.0000'))
        if self.instance.density_volume_graduation_l is None:
            self.initial.setdefault('density_volume_graduation_l', Decimal('0.0100'))
        if self.instance.density_mds_resolution_gcm3 is None:
            self.initial.setdefault('density_mds_resolution_gcm3', Decimal('0.0010'))
        if self.instance.density_k_factor is None:
            self.initial.setdefault('density_k_factor', Decimal('2.000'))

        if not self.is_bound:
            for index in range(1, 4):
                standard = getattr(self.instance, f'density_standard_{index}', None)
                if standard and standard.nominal_volume_l is not None:
                    before_field = f'density_before_volume_{index}_l'
                    after_field = f'density_after_volume_{index}_l'
                    if self.initial.get(before_field) in (None, '') and getattr(self.instance, before_field) is None:
                        self.initial[before_field] = standard.nominal_volume_l
                    if self.initial.get(after_field) in (None, '') and getattr(self.instance, after_field) is None:
                        self.initial[after_field] = standard.nominal_volume_l


class ValidationForm(forms.Form):
    class DecisionChoices:
        APPROVE = 'approve'
        REWORK = 'rework'
        CHOICES = (
            (APPROVE, 'Aprovar formulário'),
            (REWORK, 'Reprovado - Refazer'),
        )

    validator_name = forms.CharField(label='Responsável pela validação', max_length=120)
    decision = forms.ChoiceField(
        label='Decisão da validação',
        choices=DecisionChoices.CHOICES,
        initial=DecisionChoices.APPROVE,
    )
    feedback = forms.CharField(
        label='Observação do avaliador',
        required=False,
        widget=forms.Textarea(attrs={'rows': 3, 'placeholder': 'Obrigatório quando solicitar refação.'}),
    )
    signature_data = forms.CharField(widget=forms.HiddenInput())
    confirm = forms.BooleanField(label='Confirmo a validação do formulário')

    def clean_signature_data(self):
        value = self.cleaned_data['signature_data']
        if not value or not value.startswith('data:image/png;base64,'):
            raise forms.ValidationError('A assinatura é obrigatória.')
        return value

    def clean(self):
        cleaned = super().clean()
        decision = cleaned.get('decision')
        feedback = (cleaned.get('feedback') or '').strip()
        if decision == self.DecisionChoices.REWORK and not feedback:
            self.add_error('feedback', 'Informe o motivo da refação.')
        return cleaned

