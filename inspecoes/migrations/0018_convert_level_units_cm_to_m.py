from decimal import Decimal

from django.db import migrations


def convert_cm_to_m(apps, schema_editor):
    EquipmentFormCriteria = apps.get_model('inspecoes', 'EquipmentFormCriteria')
    FormSubmission = apps.get_model('inspecoes', 'FormSubmission')
    factor = Decimal('100')

    for cfg in EquipmentFormCriteria.objects.all().iterator():
        changed = False
        if cfg.acceptance_criterion_unit == 'cm':
            if cfg.acceptance_criterion_value is not None:
                cfg.acceptance_criterion_value = cfg.acceptance_criterion_value / factor
            cfg.acceptance_criterion_unit = 'm'
            changed = True
        if cfg.expanded_uncertainty_unit == 'cm':
            if cfg.expanded_uncertainty_value is not None:
                cfg.expanded_uncertainty_value = cfg.expanded_uncertainty_value / factor
            cfg.expanded_uncertainty_unit = 'm'
            changed = True
        if changed:
            cfg.save(
                update_fields=[
                    'acceptance_criterion_value',
                    'acceptance_criterion_unit',
                    'expanded_uncertainty_value',
                    'expanded_uncertainty_unit',
                    'updated_at',
                ]
            )

    for submission in FormSubmission.objects.all().iterator():
        changed = False
        if submission.acceptance_criterion_unit == 'cm':
            if submission.acceptance_criterion_pct is not None:
                submission.acceptance_criterion_pct = submission.acceptance_criterion_pct / factor
            if submission.error_before_pct is not None:
                submission.error_before_pct = submission.error_before_pct / factor
            if submission.error_after_pct is not None:
                submission.error_after_pct = submission.error_after_pct / factor
            submission.acceptance_criterion_unit = 'm'
            changed = True

        if submission.expanded_uncertainty_unit == 'cm':
            if submission.expanded_uncertainty_pct is not None:
                submission.expanded_uncertainty_pct = submission.expanded_uncertainty_pct / factor
            if submission.expanded_uncertainty_calc_pct is not None:
                submission.expanded_uncertainty_calc_pct = submission.expanded_uncertainty_calc_pct / factor
            submission.expanded_uncertainty_unit = 'm'
            changed = True

        if changed:
            submission.save(
                update_fields=[
                    'acceptance_criterion_pct',
                    'acceptance_criterion_unit',
                    'expanded_uncertainty_pct',
                    'expanded_uncertainty_unit',
                    'expanded_uncertainty_calc_pct',
                    'error_before_pct',
                    'error_after_pct',
                    'updated_at',
                ]
            )


def reverse_m_to_cm(apps, schema_editor):
    EquipmentFormCriteria = apps.get_model('inspecoes', 'EquipmentFormCriteria')
    FormSubmission = apps.get_model('inspecoes', 'FormSubmission')
    factor = Decimal('100')

    for cfg in EquipmentFormCriteria.objects.all().iterator():
        changed = False
        if cfg.acceptance_criterion_unit == 'm':
            if cfg.acceptance_criterion_value is not None:
                cfg.acceptance_criterion_value = cfg.acceptance_criterion_value * factor
            cfg.acceptance_criterion_unit = 'cm'
            changed = True
        if cfg.expanded_uncertainty_unit == 'm':
            if cfg.expanded_uncertainty_value is not None:
                cfg.expanded_uncertainty_value = cfg.expanded_uncertainty_value * factor
            cfg.expanded_uncertainty_unit = 'cm'
            changed = True
        if changed:
            cfg.save(
                update_fields=[
                    'acceptance_criterion_value',
                    'acceptance_criterion_unit',
                    'expanded_uncertainty_value',
                    'expanded_uncertainty_unit',
                    'updated_at',
                ]
            )

    for submission in FormSubmission.objects.all().iterator():
        changed = False
        if submission.acceptance_criterion_unit == 'm':
            if submission.acceptance_criterion_pct is not None:
                submission.acceptance_criterion_pct = submission.acceptance_criterion_pct * factor
            if submission.error_before_pct is not None:
                submission.error_before_pct = submission.error_before_pct * factor
            if submission.error_after_pct is not None:
                submission.error_after_pct = submission.error_after_pct * factor
            submission.acceptance_criterion_unit = 'cm'
            changed = True

        if submission.expanded_uncertainty_unit == 'm':
            if submission.expanded_uncertainty_pct is not None:
                submission.expanded_uncertainty_pct = submission.expanded_uncertainty_pct * factor
            if submission.expanded_uncertainty_calc_pct is not None:
                submission.expanded_uncertainty_calc_pct = submission.expanded_uncertainty_calc_pct * factor
            submission.expanded_uncertainty_unit = 'cm'
            changed = True

        if changed:
            submission.save(
                update_fields=[
                    'acceptance_criterion_pct',
                    'acceptance_criterion_unit',
                    'expanded_uncertainty_pct',
                    'expanded_uncertainty_unit',
                    'expanded_uncertainty_calc_pct',
                    'error_before_pct',
                    'error_after_pct',
                    'updated_at',
                ]
            )


class Migration(migrations.Migration):
    dependencies = [
        ('inspecoes', '0017_alter_equipment_acceptance_criterion_pct_and_more'),
    ]

    operations = [
        migrations.RunPython(convert_cm_to_m, reverse_m_to_cm),
    ]
