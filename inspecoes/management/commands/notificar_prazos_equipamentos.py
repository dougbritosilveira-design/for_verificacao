from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand

from inspecoes.models import Equipment


class Command(BaseCommand):
    help = 'Envia notificações por e-mail para equipamentos próximos do vencimento ou vencidos.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Não envia e-mail. Apenas mostra o que seria enviado.',
        )
        parser.add_argument(
            '--only-overdue',
            action='store_true',
            help='Envia notificação somente para equipamentos vencidos/atrasados.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        only_overdue = options['only_overdue']

        equipments = Equipment.objects.filter(active=True).order_by('tag')
        scanned = 0
        notified = 0
        skipped = 0

        for equipment in equipments:
            scanned += 1
            if equipment.deadline_status_code not in {'due_soon', 'overdue'}:
                skipped += 1
                continue
            if only_overdue and equipment.deadline_status_code != 'overdue':
                skipped += 1
                continue
            if not equipment.notification_recipients:
                skipped += 1
                self.stdout.write(
                    self.style.WARNING(
                        f'[{equipment.tag}] sem e-mails de notificação cadastrados.'
                    )
                )
                continue

            subject = f'[Verificação Balança] {equipment.tag} - {equipment.deadline_status_label}'
            message = self._build_message(equipment)

            if dry_run:
                notified += 1
                self.stdout.write(f'[DRY-RUN] {equipment.tag} -> {", ".join(equipment.notification_recipients)}')
                continue

            send_mail(
                subject=subject,
                message=message,
                from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', None),
                recipient_list=equipment.notification_recipients,
                fail_silently=False,
            )
            notified += 1
            self.stdout.write(
                self.style.SUCCESS(
                    f'Notificação enviada: {equipment.tag} -> {", ".join(equipment.notification_recipients)}'
                )
            )

        self.stdout.write(
            self.style.SUCCESS(
                f'Processamento concluído. Equipamentos analisados: {scanned}, notificações: {notified}, ignorados: {skipped}.'
            )
        )

    def _build_message(self, equipment):
        last_visit = equipment.last_visit_date or '-'
        next_visit = equipment.next_visit_due_date or '-'
        return (
            'Notificação automática de prazo de verificação/ajuste de balança dinâmica.\n\n'
            f'Equipamento: {equipment.tag} - {equipment.description}\n'
            f'Local: {equipment.location}\n'
            f'Status do prazo: {equipment.deadline_status_label}\n'
            f'Detalhe: {equipment.deadline_status_detail}\n'
            f'Periodicidade configurada: {equipment.revisit_interval_days or "-"} dia(s)\n'
            f'Última visita registrada: {last_visit}\n'
            f'Próxima visita prevista: {next_visit}\n\n'
            'Acesse o app para registrar uma nova verificação/ajuste e gerar o formulário.\n'
        )
