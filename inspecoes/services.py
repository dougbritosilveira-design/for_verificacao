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
from django.contrib.staticfiles import finders
from django.utils import timezone

from .models import FormSubmission


def _format_num(value, decimals=2):
    if value is None or value == '':
        return '-'
    try:
        quant = Decimal('1').scaleb(-decimals)
        number = Decimal(str(value)).quantize(quant, rounding=ROUND_HALF_UP)
        return f'{number:.{decimals}f}'.replace('.', ',')
    except (TypeError, ValueError, InvalidOperation):
        return str(value)


def _format_datetime(value):
    if not value:
        return '-'
    try:
        return timezone.localtime(value).strftime('%d/%m/%Y %H:%M')
    except Exception:
        return str(value)


def _acceptance_label_for_value(value, limit):
    if value is None or limit in (None, ''):
        return 'Pendente'
    try:
        value_abs = abs(Decimal(str(value)))
        limit_dec = Decimal(str(limit))
    except (InvalidOperation, TypeError, ValueError):
        return 'Pendente'
    return 'Aprovado' if value_abs <= limit_dec else 'Reprovado'


def _decode_logo_base64():
    raw_logo = str(getattr(settings, 'HYDRO_LOGO_BASE64', '') or '').strip()
    if not raw_logo:
        return None

    encoded = raw_logo
    if ',' in raw_logo:
        prefix, payload = raw_logo.split(',', 1)
        if 'base64' in prefix.lower():
            encoded = payload

    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        return None

    return data or None


def _resolve_logo_path() -> Path | None:
    configured_logo_path = str(getattr(settings, 'HYDRO_LOGO_PATH', '') or '').strip()
    candidates: list[Path] = []
    if configured_logo_path:
        candidates.append(Path(configured_logo_path))

    base_dir = Path(getattr(settings, 'BASE_DIR', '.'))
    candidates.extend(
        [
            base_dir / 'static' / 'branding' / 'hydro_logo.png',
            base_dir / 'static' / 'branding' / 'hydro_logo.jpg',
            base_dir / 'static' / 'branding' / 'hydro_logo.jpeg',
            base_dir / 'static' / 'branding' / 'hydro_logo_vertical_black.png',
            base_dir / 'static' / 'img' / 'hydro_logo.png',
            base_dir / 'staticfiles' / 'branding' / 'hydro_logo.png',
            base_dir / 'staticfiles' / 'branding' / 'hydro_logo.jpg',
            base_dir / 'staticfiles' / 'branding' / 'hydro_logo.jpeg',
            base_dir / 'hydro_logo.png',
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

    static_candidates = [
        'branding/hydro_logo.png',
        'branding/hydro_logo.jpg',
        'branding/hydro_logo.jpeg',
        'branding/hydro_logo_vertical_black.png',
        'img/hydro_logo.png',
    ]
    for static_name in static_candidates:
        found_path = finders.find(static_name)
        if not found_path:
            continue
        if isinstance(found_path, (list, tuple)):
            found_path = next((p for p in found_path if p), '')
        if found_path:
            return Path(found_path)

    return None


def _resolve_logo_image_reader(image_reader_cls):
    if image_reader_cls is None:
        return None

    logo_data = _decode_logo_base64()
    if logo_data:
        try:
            return image_reader_cls(io.BytesIO(logo_data))
        except Exception:
            pass

    logo_path = _resolve_logo_path()
    if logo_path:
        try:
            return image_reader_cls(str(logo_path))
        except Exception:
            pass
    return None


def _draw_pdf_header(pdf, page_width, page_height, image_reader_cls=None):
    margin_x = 40
    top_y = page_height - 40
    header_bottom = page_height - 105

    logo_rendered = False
    logo_reader = _resolve_logo_image_reader(image_reader_cls)
    if logo_reader is not None:
        try:
            pdf.drawImage(
                logo_reader,
                margin_x,
                page_height - 90,
                width=72,
                height=46,
                preserveAspectRatio=True,
                mask='auto',
            )
            logo_rendered = True
        except Exception:
            pass

    text_x = margin_x + (86 if logo_rendered else 0)
    if not logo_rendered:
        pdf.setFont('Helvetica-Bold', 18)
        pdf.drawString(margin_x, page_height - 66, 'Hydro')
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
    form_code = submission.form_type.code if submission.form_type_id and submission.form_type else 'FOR 08.05.003'
    form_code = re.sub(r'[^A-Za-z0-9._-]+', '-', str(form_code)).strip('-') or 'formulario'
    om = re.sub(r'[^A-Za-z0-9._-]+', '-', str(submission.om_number or '')).strip('-') or 'sem-om'
    tag = re.sub(r'[^A-Za-z0-9._-]+', '-', str(submission.equipment.tag or '')).strip('-') or 'sem-tag'
    return f'{form_code}_OM_{om}_{tag}.pdf'


def _generate_submission_report_pdf_bytes(
    submission: FormSubmission,
    *,
    include_signature: bool = True,
) -> bytes:
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.utils import ImageReader
        from reportlab.pdfgen import canvas
    except Exception:
        text = (
            f'FORMULARIO OM {submission.om_number}\n'
            f'EQUIPAMENTO: {submission.equipment.tag}\n'
            f'LOCAL: {submission.location_snapshot}\n'
            f'VALIDADO POR: {submission.validator_name}\n'
        )
        return text.encode('utf-8')

    buffer = io.BytesIO()
    page_width, page_height = A4
    pdf = canvas.Canvas(buffer, pagesize=A4)
    y = _draw_pdf_header(pdf, page_width, page_height, ImageReader)
    line_height = 16

    acceptance_limit = submission.acceptance_limit_pct
    error_before_value = submission.acceptance_error_before_value
    error_after_value = submission.acceptance_error_after_value
    error_after_abs = submission.acceptance_error_after_abs
    error_before_status = _acceptance_label_for_value(error_before_value, acceptance_limit)
    error_after_status = _acceptance_label_for_value(error_after_value, acceptance_limit)
    uncertainty_calc = submission.expanded_uncertainty_calc_value
    uncertainty_status = submission.expanded_uncertainty_status_label
    combined_value = submission.acceptance_combined_value
    combined_status = submission.acceptance_status_label
    form_code = submission.form_type.code if submission.form_type_id and submission.form_type else 'FOR 08.05.003'
    form_title = (
        submission.form_type.title
        if submission.form_type_id and submission.form_type
        else 'Verificacao e ajuste de balanca dinamica (MVP)'
    )
    acceptance_unit = submission.acceptance_unit_label or '%'
    uncertainty_unit = submission.expanded_uncertainty_unit_label or acceptance_unit

    if submission.is_scanner_form:
        lines = [
            f'{form_code} - {form_title}',
            f'Data da visita: {submission.execution_date}',
            f'OM: {submission.om_number}',
            f'Equipamento: {submission.equipment.tag} - {submission.equipment.description}',
            f'Local: {submission.location_snapshot}',
            f'Executor: {submission.executor_name}',
            f'Certificado: {Path(submission.scanner_certificate_file.name).name if submission.scanner_certificate_file else "-"}',
            f'Número do certificado: {submission.scanner_certificate_number or "-"}',
            f'Modelo: {submission.scanner_model or "-"}',
            f'Série: {submission.scanner_serial_number or "-"}',
            f'Laboratório/fornecedor: {submission.scanner_provider or "-"}',
            f'Data da medição no certificado: {submission.scanner_measurement_date or "-"}',
            f'Critério fixo ({acceptance_unit}): {_format_num(acceptance_limit, 2)}',
            f'Parcela do fabricante (ppm): {_format_num(submission.scanner_manufacturer_ppm_value, 3)}',
            f'Incerteza calculada ({uncertainty_unit}): {_format_num(uncertainty_calc, 3)}',
            f'Status da incerteza: {uncertainty_status}',
            f'Erro máximo absoluto ({acceptance_unit}): {_format_num(submission.scanner_max_error_abs_mm, 3)}',
            f'Soma final |erro| + U(e) ({acceptance_unit}): {_format_num(combined_value, 3)}',
            f'Status final (critério fixo): {combined_status}',
            f'Status critério fabricante: {submission.scanner_status_manufacturer}',
            '',
            'Pontos avaliados (nominal/medido/erro abs em mm):',
        ]
        for row in submission.scanner_points:
            lines.append(
                f'Ponto {row["index"]} ({row["target"]}): '
                f'N={_format_num(row["nominal_m"], 3)} m | '
                f'M={_format_num(row["measured_m"], 3)} m | '
                f'Erro={_format_num(row["error_abs_mm"], 3)} mm | '
                f'CA fab={_format_num(row["ca_manufacturer_mm"], 3)} mm | '
                f'Fixo={"OK" if row["ok_fixed"] else ("N/A" if row["ok_fixed"] is None else "NOK")} | '
                f'Fabricante={"OK" if row["ok_manufacturer"] else ("N/A" if row["ok_manufacturer"] is None else "NOK")}'
            )
        lines.extend(
            [
                '',
                f'Setor 1: {submission.sector or ""}',
                f'Setor 2: {submission.sector_2 or ""}',
                f'Setor 3: {submission.sector_3 or ""}',
                f'Nome 1 / Matrícula 1: {submission.technician_1_name or ""} ({submission.validator_registration or ""})',
                f'Nome 2 / Matrícula 2: {submission.technician_2_name or ""} ({submission.technician_2_registration or ""})',
                f'Nome 3 / Matrícula 3: {submission.technician_3_name or ""} ({submission.technician_3_registration or ""})',
                f'Padrões utilizados: {submission.standards_used or ""}',
                f'Observação: {submission.observation or ""}',
                '',
                f'Validado por: {submission.validator_name or "-"}',
                f'Validado em: {_format_datetime(submission.validated_at)}',
            ]
        )
    elif submission.is_level_form:
        before_combined = submission.level_before_combined_value
        before_status = (
            'Pendente dados'
            if submission.level_before_combined_ok is None
            else ('Aprovado' if submission.level_before_combined_ok else 'Reprovado')
        )
        after_combined = submission.level_after_combined_value
        after_status = (
            'Pendente dados'
            if submission.level_after_combined_ok is None
            else ('Aprovado' if submission.level_after_combined_ok else 'Reprovado')
        )
        lines = [
            f'{form_code} - {form_title}',
            f'Data da visita: {submission.execution_date}',
            f'OM: {submission.om_number}',
            f'Equipamento: {submission.equipment.tag} - {submission.equipment.description}',
            f'Local: {submission.location_snapshot}',
            f'Executor: {submission.executor_name}',
            f'Fase final considerada: {submission.level_final_phase_label}',
            f'Critério de aceitação ({acceptance_unit}): {_format_num(acceptance_limit, 2)}',
            f'Incerteza expandida calculada ({uncertainty_unit}): {_format_num(uncertainty_calc, 3)}',
            f'Status da incerteza expandida: {uncertainty_status}',
            f'Erro antes ({acceptance_unit}): {_format_num(error_before_value, 3)}',
            f'U(e) antes ({uncertainty_unit}): {_format_num(submission.level_uncertainty_expanded_before_m, 3)}',
            f'Soma antes |erro| + U(e) ({acceptance_unit}): {_format_num(before_combined, 3)}',
            f'Status antes: {before_status} (limite <= {_format_num(acceptance_limit, 2)}{acceptance_unit})',
            f'Erro final ({acceptance_unit}): {_format_num(error_after_value, 3)}',
            f'|Erro final| ({acceptance_unit}): {_format_num(error_after_abs, 3)}',
            f'Status erro final: {error_after_status} (limite <= {_format_num(acceptance_limit, 2)}{acceptance_unit})',
            f'Erro depois ({acceptance_unit}): {_format_num(submission.level_after_mean_abs_m, 3)}',
            f'U(e) depois ({uncertainty_unit}): {_format_num(submission.level_uncertainty_expanded_after_m, 3)}',
            f'Soma depois |erro| + U(e) ({acceptance_unit}): {_format_num(after_combined, 3)}',
            f'Status depois: {after_status} (limite <= {_format_num(acceptance_limit, 2)}{acceptance_unit})',
            f'Soma final |erro| + U(e) ({acceptance_unit}): {_format_num(combined_value, 3)}',
            f'Status final: {combined_status} (limite <= {_format_num(acceptance_limit, 2)}{acceptance_unit})',
            '',
            'Verificação antes do ajuste (VM, VL, erro abs em m):',
        ]
        for row in submission.level_before_rows:
            lines.append(
                f'Ponto {row["index"]}: VM={_format_num(row["vm"], 3)} | VL={_format_num(row["vl"], 3)} | Erro abs={_format_num(row["error_abs_m"], 3)}'
            )
        if submission.level_has_after_measurements:
            lines.append('')
            lines.append('Verificação após ajuste (VM, VL, erro abs em m):')
            for row in submission.level_after_rows:
                lines.append(
                    f'Ponto {row["index"]}: VM={_format_num(row["vm"], 3)} | VL={_format_num(row["vl"], 3)} | Erro abs={_format_num(row["error_abs_m"], 3)}'
                )
        lines.extend(
            [
                '',
                f'Setor 1: {submission.sector or ""}',
                f'Setor 2: {submission.sector_2 or ""}',
                f'Setor 3: {submission.sector_3 or ""}',
                f'Nome 1 / Matrícula 1: {submission.technician_1_name or ""} ({submission.validator_registration or ""})',
                f'Nome 2 / Matrícula 2: {submission.technician_2_name or ""} ({submission.technician_2_registration or ""})',
                f'Nome 3 / Matrícula 3: {submission.technician_3_name or ""} ({submission.technician_3_registration or ""})',
                f'Observação: {submission.observation or ""}',
                '',
                f'Validado por: {submission.validator_name or "-"}',
                f'Validado em: {_format_datetime(submission.validated_at)}',
            ]
        )
    else:
        lines = [
            f'{form_code} - {form_title}',
            f'Data da visita: {submission.execution_date}',
            f'OM: {submission.om_number}',
            f'Equipamento: {submission.equipment.tag} - {submission.equipment.description}',
            f'Local: {submission.location_snapshot}',
            f'Houve troca de correia: {"Sim" if submission.belt_replaced else "Nao"}',
            f'Executor: {submission.executor_name}',
            f'Critério de aceitação ({acceptance_unit}): {_format_num(acceptance_limit, 1)}',
            f'Incerteza expandida calculada ({uncertainty_unit}): {_format_num(uncertainty_calc, 2)}',
            f'Status da incerteza expandida: {uncertainty_status}',
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
            f'Erro antes ({acceptance_unit}): {_format_num(error_before_value, 2)}',
            f'Status erro antes: {error_before_status} (limite <= {_format_num(acceptance_limit, 1)}{acceptance_unit})',
            f'IL depois: {_format_num(submission.il_after, 2)}',
            f'Erro depois ({acceptance_unit}): {_format_num(error_after_value, 2)}',
            f'|Erro final| ({acceptance_unit}): {_format_num(error_after_abs, 2)}',
            f'Status erro depois: {error_after_status} (limite <= {_format_num(acceptance_limit, 1)}{acceptance_unit})',
            f'Soma final |erro| + U(e) ({acceptance_unit}): {_format_num(combined_value, 2)}',
            f'Status final: {combined_status} (limite <= {_format_num(acceptance_limit, 1)}{acceptance_unit})',
            f'Setor 1: {submission.sector or ""}',
            f'Setor 2: {submission.sector_2 or ""}',
            f'Setor 3: {submission.sector_3 or ""}',
            f'Nome 1 / Matrícula 1: {submission.technician_1_name or ""} ({submission.validator_registration or ""})',
            f'Nome 2 / Matrícula 2: {submission.technician_2_name or ""} ({submission.technician_2_registration or ""})',
            f'Nome 3 / Matrícula 3: {submission.technician_3_name or ""} ({submission.technician_3_registration or ""})',
            f'Padrões utilizados: {submission.standards_used or ""}',
            f'Observação: {submission.observation or ""}',
            '',
            f'Validado por: {submission.validator_name or "-"}',
            f'Validado em: {_format_datetime(submission.validated_at)}',
        ]

    pdf.setFont('Helvetica', 10)
    for line in lines:
        if y < 60:
            pdf.showPage()
            y = _draw_pdf_header(pdf, page_width, page_height, ImageReader)
            pdf.setFont('Helvetica', 10)
        pdf.drawString(40, y, line[:120])
        y -= line_height

    if include_signature:
        signature_reader = _decode_signature_image(submission.validator_signature_data, ImageReader)
        signature_block_height = 108
        if y < 60 + signature_block_height:
            pdf.showPage()
            y = _draw_pdf_header(pdf, page_width, page_height, ImageReader)

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


def _build_scanner_signature_page_pdf_bytes(submission: FormSubmission) -> bytes | None:
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.utils import ImageReader
        from reportlab.pdfgen import canvas
    except Exception:
        return None

    buffer = io.BytesIO()
    page_width, page_height = A4
    pdf = canvas.Canvas(buffer, pagesize=A4)
    y = _draw_pdf_header(pdf, page_width, page_height, ImageReader)

    pdf.setFont('Helvetica-Bold', 12)
    pdf.drawString(40, y, 'Assinatura do validador (página final)')
    y -= 20
    pdf.setFont('Helvetica', 10)
    pdf.drawString(40, y, f'Formulário: {submission.form_type_label}')
    y -= 14
    pdf.drawString(40, y, f'OM: {submission.om_number} | Equipamento: {submission.equipment.tag}')
    y -= 14
    pdf.drawString(40, y, f'Validador: {submission.validator_name or "-"}')
    y -= 14
    pdf.drawString(40, y, f'Validado em: {_format_datetime(submission.validated_at)}')
    y -= 22

    signature_reader = _decode_signature_image(submission.validator_signature_data, ImageReader)
    box_w = 420
    box_h = 180
    box_x = 40
    box_y = max(80, y - box_h)
    pdf.rect(box_x, box_y, box_w, box_h)
    if signature_reader is not None:
        try:
            pdf.drawImage(
                signature_reader,
                box_x + 6,
                box_y + 6,
                width=box_w - 12,
                height=box_h - 12,
                preserveAspectRatio=True,
                mask='auto',
            )
        except Exception:
            pdf.setFont('Helvetica', 10)
            pdf.drawString(box_x + 10, box_y + (box_h / 2), 'Falha ao renderizar assinatura')
    else:
        pdf.setFont('Helvetica', 10)
        pdf.drawString(box_x + 10, box_y + (box_h / 2), 'Assinatura não disponível')

    pdf.save()
    return buffer.getvalue()


def _merge_scanner_report_with_certificate(
    report_pdf_bytes: bytes,
    submission: FormSubmission,
) -> bytes | None:
    if not submission.scanner_certificate_file:
        return None

    try:
        from pypdf import PdfReader, PdfWriter
    except Exception:
        return None

    try:
        writer = PdfWriter()

        report_reader = PdfReader(io.BytesIO(report_pdf_bytes))
        for page in report_reader.pages:
            writer.add_page(page)

        with submission.scanner_certificate_file.open('rb') as certificate_file:
            cert_reader = PdfReader(certificate_file)
            if cert_reader.is_encrypted:
                try:
                    cert_reader.decrypt('')
                except Exception:
                    pass
            for page in cert_reader.pages:
                writer.add_page(page)

        signature_page = _build_scanner_signature_page_pdf_bytes(submission)
        if signature_page:
            signature_reader = PdfReader(io.BytesIO(signature_page))
            for page in signature_reader.pages:
                writer.add_page(page)

        output = io.BytesIO()
        writer.write(output)
        return output.getvalue()
    except Exception:
        return None


def generate_submission_pdf_bytes(submission: FormSubmission) -> bytes:
    if submission.scanner_certificate_file:
        report_without_signature = _generate_submission_report_pdf_bytes(
            submission,
            include_signature=False,
        )
        merged_pdf = _merge_scanner_report_with_certificate(report_without_signature, submission)
        if merged_pdf:
            return merged_pdf
        # fallback seguro caso merge falhe
        return _generate_submission_report_pdf_bytes(submission, include_signature=True)

    return _generate_submission_report_pdf_bytes(submission, include_signature=True)


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
    form_code = submission.form_type.code if submission.form_type_id and submission.form_type else 'FOR 08.05.003'
    form_code_payload = re.sub(r'\s+', '_', str(form_code).strip())
    data = {
        'maintenance_order': submission.om_number,
        'equipment_tag': submission.equipment.tag,
        'form_code': form_code_payload,
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

