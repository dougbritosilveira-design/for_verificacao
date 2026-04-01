from django.db import migrations


def create_humidity_form_type(apps, schema_editor):
    InspectionFormType = apps.get_model('inspecoes', 'InspectionFormType')
    InspectionFormType.objects.get_or_create(
        code='FOR UMIDADE',
        defaults={
            'title': 'Validação de certificado de calibração de balança de umidade (MVP)',
            'description': 'Formulário para análise de certificado de calibração de balança de umidade.',
            'active': True,
        },
    )


def reverse_create_humidity_form_type(apps, schema_editor):
    InspectionFormType = apps.get_model('inspecoes', 'InspectionFormType')
    InspectionFormType.objects.filter(code='FOR UMIDADE').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('inspecoes', '0030_equipmentformcriteria_certificate_points_limit_and_more'),
    ]

    operations = [
        migrations.RunPython(
            create_humidity_form_type,
            reverse_code=reverse_create_humidity_form_type,
        ),
    ]
