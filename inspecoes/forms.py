from django import forms

from .models import Equipment, FormSubmission


class SelectionForm(forms.ModelForm):
    equipment = forms.ModelChoiceField(
        queryset=Equipment.objects.filter(active=True),
        label='Equipamento',
        empty_label='Selecione o equipamento',
    )

    class Meta:
        model = FormSubmission
        fields = ['equipment', 'location_snapshot', 'om_number', 'execution_date', 'executor_name']
        widgets = {'execution_date': forms.DateInput(attrs={'type': 'date'})}
        labels = {
            'location_snapshot': 'Local',
            'om_number': 'Nº OM',
            'execution_date': 'Data da visita',
            'executor_name': 'Responsável pela verificação',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['location_snapshot'].widget.attrs.update(
            {
                'readonly': 'readonly',
                'title': 'Campo preenchido automaticamente conforme o equipamento selecionado.',
                'style': 'background:#f3f0e6;',
            }
        )

    def clean(self):
        cleaned_data = super().clean()
        equipment = cleaned_data.get('equipment')
        if equipment:
            cleaned_data['location_snapshot'] = equipment.location
        return cleaned_data


class TechnicalForm(forms.ModelForm):
    class Meta:
        model = FormSubmission
        fields = [
            't1', 't2', 't3',
            'm1', 'm2', 'm3', 'mark_distance',
            'pulses_per_turn_1', 'pulses_per_turn_2', 'pulses_per_turn_3', 'ibm',
            'speed_characteristic_b04',
            'abw_1', 'abw_2', 'abw_3', 'tare_1', 'tare_2', 'tare_3',
            'applied_weight', 'bridge_length', 'belt_length', 'belt_speed_v',
            'il_before_ti', 'il_before_tf', 'il_after_ti', 'il_after_tf',
            'check_weight', 'kor', 'acceptance_criterion_pct', 'expanded_uncertainty_pct',
            'calculated_flow_ic', 'error_before_pct', 'error_after_pct',
            'sector', 'sector_2', 'sector_3',
            'validator_registration',
            'technician_1_name',
            'technician_2_name', 'technician_2_registration',
            'technician_3_name', 'technician_3_registration',
            'observation',
        ]
        labels = {
            't1': 'T1 (s)',
            't2': 'T2 (s)',
            't3': 'T3 (s)',
            'm1': 'M1 (s)',
            'm2': 'M2 (s)',
            'm3': 'M3 (s)',
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
            'expanded_uncertainty_pct': 'Incerteza expandida (%)',
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
            'observation': 'Observação',
        }
        widgets = {'observation': forms.Textarea(attrs={'rows': 4})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field, (forms.DecimalField, forms.FloatField, forms.IntegerField)):
                field.widget.attrs.update({'step': '0.001', 'inputmode': 'decimal'})

        for name in [
            'ibm',
            'belt_speed_v',
            'belt_length',
            'speed_characteristic_b04',
            'calculated_flow_ic',
            'error_before_pct',
            'error_after_pct',
            'acceptance_criterion_pct',
            'expanded_uncertainty_pct',
        ]:
            self.fields[name].disabled = True
            self.fields[name].widget.attrs.update(
                {
                    'style': 'background:#f3f0e6;',
                    'title': 'Campo calculado automaticamente pelo sistema.',
                }
            )


class ValidationForm(forms.Form):
    class DecisionChoices:
        APPROVE = 'approve'
        REWORK = 'rework'
        CHOICES = (
            (APPROVE, 'Aprovar formulário'),
            (REWORK, 'Solicitar refação'),
        )

    validator_name = forms.CharField(label='Responsável pela validação', max_length=120)
    decision = forms.ChoiceField(
        label='Decisão da validação',
        choices=DecisionChoices.CHOICES,
        initial=DecisionChoices.APPROVE,
    )
    feedback = forms.CharField(
        label='Observação do validador',
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
