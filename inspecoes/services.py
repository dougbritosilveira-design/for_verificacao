from __future__ import annotations

import base64
import binascii
import io
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Tuple

import requests
from django.conf import settings
from django.utils import timezone

from .models import FormSubmission


def _format_num(value, decimals=2):
    if value is None or value == '':
        return ''
    try:
        quant = Decimal('1').scaleb(-decimals)
        number = Decimal(str(value)).quantize(quant, rounding=ROUND_HALF_UP)
        return f'{number:.{decimals}f}'.replace('.', ',')
    except (TypeError, ValueError, InvalidOperation):
        return str(value)


def _resolve_logo_path() -> Path | None:
    configured_logo_path = str(getattr(settings, 'HYDRO_LOGO_PATH', '') or '').strip()
    candidates: list[Path] = []
    if configured_logo_path:
        candidates.append(Path(configured_logo_path))

    base_dir = Path(getattr(settings, 'BASE_DIR', '.'))
    candidates.extend(
        [
            base_dir / 'static' / 'branding' / 'hydro_logo.png',
            base_dir / 'static' / 'branding' / 'hydro_logo_vertical_black.png',
            Path(
                r'C:\Users\a824147\Downloads\hydro-logo-vertical\Hydro logo vertical\hydro_logo_vertical_black.png'
            ),
        ]
    )

    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


def _draw_pdf_header(pdf, page_width, page_height):
    margin_x = 40
    top_y = page_height - 40
    header_bottom = page_height - 105

    logo_path = _resolve_logo_path()
    if logo_path:
        try:
            pdf.drawImage(
                str(logo_path),
                margin_x,
                page_height - 90,
                width=72,
                height=46,
                preserveAspectRatio=True,
                mask='auto',
            )
        except Exception:
            pass

    text_x = margin_x + 86
    pdf.setFont('Helvetica-Bold', 14)
    pdf.drawString(text_x, top_y - 4, 'Hydro MPSA')
    pdf.setFont('Helvetica', 10)
    pdf.drawString(text_x, top_y - 21, 'Formulário interno de Verificação/Ajuste')
    pdf.setFont('Helvetica', 8)
    pdf.drawRightString(
        page_width - margin_x,
        top_y - 4,
        f'Gerado em: {timezone.localtime().strftime("%d/%m/%Y %H:%M")}',
    )
    pdf.line(margin_x, header_bottom, page_width - margin_x, header_bottom)
    return header_bottom - 20


def _decode_signature_image(signature_data, image_reader_cls):
    if not signature_data or not isinstance(signature_data, str):
        return None
    if ',' not in signature_data:
        return None

    header, encoded = signature_data.split(',', 1)
    if 'base64' not in header.lower():
        return None

    try:
        raw_bytes = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        return None

    if not raw_bytes:
        return None
    try:
        return image_reader_cls(io.BytesIO(raw_bytes))
    except Exception:
        return None


def build_submission_pdf_filename(submission: FormSubmission) -> str:
    om = re.sub(r'[^A-Za-z0-9._-]+', '-', str(submission.om_number or '')).strip('-') or 'sem-om'
    tag = re.sub(r'[^A-Za-z0-9._-]+', '-', str(submission.equipment.tag or '')).strip('-') or 'sem-tag'
    return f'FOR_08.05.003_OM_{om}_{tag}.pdf'


def generate_submission_pdf_bytes(submission: FormSubmission) -> bytes:
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.utils import ImageReader
        from reportlab.pdfgen import canvas
    except Exception:
        text = (
            f'FORMULÁRIO OM {submission.om_number}\n'
            f'EQUIPAMENTO: {submission.equipment.tag}\n'
            f'LOCAL: {submission.location_snapshot}\n'
            f'VALIDADO POR: {submission.validator_name}\n'
        )
        return text.encode('utf-8')

    buffer = io.BytesIO()
    page_width, page_height = A4
    pdf = canvas.Canvas(buffer, pagesize=A4)
    y = _draw_pdf_header(pdf, page_width, page_height)
    line_height = 16

    lines = [
        'FOR 08.05.003 - Verificação e ajuste de balança dinâmica (MVP)',
        f'Data da visita: {submission.execution_date}',
        f'OM: {submission.om_number}',
        f'Equipamento: {submission.equipment.tag} - {submission.equipment.description}',
        f'Local: {submission.location_snapshot}',
        f'Executor: {submission.executor_name}',
        f'Critério de aceitação (%): {_format_num(submission.acceptance_criterion_pct, 2)}',
        f'Incerteza expandida (%): {_format_num(submission.expanded_uncertainty_pct, 2)}',
        '',
        f'T1/T2/T3: {_format_num(submission.t1, 2)} / {_format_num(submission.t2, 2)} / {_format_num(submission.t3, 2)}',
        f'TM (média): {_format_num(submission.tm, 2)}',
        f'M1/M2/M3: {_format_num(submission.m1, 2)} / {_format_num(submission.m2, 2)} / {_format_num(submission.m3, 2)}',
        f'MD (média): {_format_num(submission.md, 2)}',
        f'Distância entre marcas: {_format_num(submission.mark_distance, 2)}',
        f'IBM (média pulsos): {_format_num(submission.ibm, 2)}',
        f'Velocidade V: {_format_num(submission.belt_speed_v, 4)}',
        f'Comprimento L: {_format_num(submission.belt_length, 2)}',
        f'B04 (IBM/L): {_format_num(submission.speed_characteristic_b04, 2)}',
        f'Ic (Q x V x 3,6): {_format_num(submission.calculated_flow_ic, 2)}',
        f'IL antes: {_format_num(submission.il_before, 2)}',
        f'Erro antes (%): {_format_num(submission.error_before_pct, 2)}',
        f'IL depois: {_format_num(submission.il_after, 2)}',
        f'Erro depois (%): {_format_num(submission.error_after_pct, 2)}',
        f'Setor 1: {submission.sector or ""}',
        f'Setor 2: {submission.sector_2 or ""}',
        f'Setor 3: {submission.sector_3 or ""}',
        f'Nome 1 / Matrícula 1: {submission.technician_1_name or ""} ({submission.validator_registration or ""})',
        f'Nome 2 / Matrícula 2: {submission.technician_2_name or ""} ({submission.technician_2_registration or ""})',
        f'Nome 3 / Matrícula 3: {submission.technician_3_name or ""} ({submission.technician_3_registration or ""})',
        f'Observação: {submission.observation or ""}',
        '',
        f'Validado por: {submission.validator_name}',
        f'Validado em: {submission.validated_at}',
    ]

    pdf.setFont('Helvetica', 10)
    for line in lines:
        if y < 60:
            pdf.showPage()
            y = _draw_pdf_header(pdf, page_width, page_height)
            pdf.setFont('Helvetica', 10)
        pdf.drawString(40, y, line[:120])
        y -= line_height

    # Bloco de assinatura do validador.
    signature_reader = _decode_signature_image(submission.validator_signature_data, ImageReader)
    signature_block_height = 108
    if y < 60 + signature_block_height:
        pdf.showPage()
        y = _draw_pdf_header(pdf, page_width, page_height)

    pdf.setFont('Helvetica-Bold', 10)
    pdf.drawString(40, y, 'Assinatura do validador:')
    y -= 12
    box_w = 240
    box_h = 90
    box_x = 40
    box_y = y - box_h
    pdf.rect(box_x, box_y, box_w, box_h)
    if signature_reader is not None:
        try:
            pdf.drawImage(
                signature_reader,
                box_x + 4,
                box_y + 4,
                width=box_w - 8,
                height=box_h - 8,
                preserveAspectRatio=True,
                mask='auto',
            )
        except Exception:
            pdf.setFont('Helvetica', 9)
            pdf.drawString(box_x + 8, box_y + (box_h / 2), 'Falha ao renderizar assinatura')
    else:
        pdf.setFont('Helvetica', 9)
        pdf.drawString(box_x + 8, box_y + (box_h / 2), 'Assinatura não disponível')

    pdf.save()
    return buffer.getvalue()


def upload_pdf_to_sap(submission: FormSubmission, pdf_bytes: bytes) -> Tuple[bool, str, str]:
    if not settings.SAP_API_BASE_URL or not settings.SAP_API_TOKEN:
        return False, '', 'SAP não configurado (defina SAP_API_BASE_URL e SAP_API_TOKEN).'

    url = f"{settings.SAP_API_BASE_URL}{settings.SAP_API_ATTACH_ENDPOINT}"
    headers = {'Authorization': f'Bearer {settings.SAP_API_TOKEN}'}
    files = {
        'file': (
            build_submission_pdf_filename(submission),
            pdf_bytes,
            'application/pdf',
        )
    }
    data = {
        'maintenance_order': submission.om_number,
        'equipment_tag': submission.equipment.tag,
        'form_code': 'FOR_08.05.003',
        'validated_by': submission.validator_name,
        'validated_at': submission.validated_at.isoformat() if submission.validated_at else '',
    }

    resp = requests.post(url, headers=headers, files=files, data=data, timeout=60, verify=settings.SAP_VERIFY_SSL)
    if 200 <= resp.status_code < 300:
        attachment_id = ''
        message = f'HTTP {resp.status_code}'
        try:
            payload = resp.json()
            attachment_id = str(payload.get('attachment_id') or payload.get('id') or '')
            message = str(payload)
        except Exception:
            message = resp.text[:1000]
        return True, attachment_id, message

    return False, '', f'HTTP {resp.status_code}: {resp.text[:1000]}'


def process_sap_submission(submission: FormSubmission) -> None:
    pdf_bytes = generate_submission_pdf_bytes(submission)
    ok, attachment_id, message = upload_pdf_to_sap(submission, pdf_bytes)
    if ok:
        submission.sap_status = FormSubmission.SapStatus.SUCCESS
        submission.sap_attachment_id = attachment_id
        submission.sap_response_message = message
        submission.sap_sent_at = timezone.now()
        submission.status = FormSubmission.Status.SENT_TO_SAP
    else:
        submission.sap_status = FormSubmission.SapStatus.FAILED
        submission.sap_response_message = message
        if submission.status != FormSubmission.Status.SENT_TO_SAP:
            submission.status = FormSubmission.Status.APPROVED
    submission.save(update_fields=['sap_status', 'sap_attachment_id', 'sap_response_message', 'sap_sent_at', 'status', 'updated_at'])
