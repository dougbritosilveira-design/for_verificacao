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
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.utils import ImageReader, simpleSplit
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

    certificate_summary_rows = []
    certificate_summary_kind = ''
    if submission.is_flow_form:
        valid_points = submission.flow_valid_points
        approved_points = sum(1 for row in valid_points if row.get('ok') is True)
        total_points = len(valid_points)
        certificate_summary_rows = [row for row in submission.flow_points if row.get('combined_pct') is not None]
        certificate_summary_kind = 'flow'
        lines = [
            f'{form_code} - {form_title}',
            f'Data da visita: {submission.execution_date}',
            f'OM: {submission.om_number}',
            f'Equipamento: {submission.equipment.tag} - {submission.equipment.description}',
            f'Local: {submission.location_snapshot}',
            f'Executor: {submission.executor_name}',
            f'Certificado: {Path(submission.flow_certificate_file.name).name if submission.flow_certificate_file else "-"}',
            f'Número do certificado: {submission.flow_certificate_number or "-"}',
            f'TAG no certificado: {submission.flow_tag_on_certificate or "-"}',
            f'Modelo medidor: {submission.flow_meter_model or "-"}',
            f'Série medidor: {submission.flow_meter_serial_number or "-"}',
            f'Modelo conversor: {submission.flow_converter_model or "-"}',
            f'Série conversor: {submission.flow_converter_serial_number or "-"}',
            f'Laboratório/fornecedor: {submission.flow_provider or "-"}',
            f'Data da calibração no certificado: {submission.flow_measurement_date or "-"}',
            f'Critério de aceitação ({acceptance_unit}): {_format_num(acceptance_limit, 1)}',
            f'U(e) máxima entre os pontos ({uncertainty_unit}): {_format_num(uncertainty_calc, 3)}',
            f'Erro máximo absoluto ({acceptance_unit}): {_format_num(submission.flow_max_error_abs_pct, 4)}',
            f'Soma final |erro| + U(e) ({acceptance_unit}): {_format_num(combined_value, 4)}',
            f'Pontos aprovados: {approved_points}/{total_points}',
            f'Status final: {combined_status}',
            '',
            'Pontos avaliados (calibração/indicado/referência/tendência/U(e)):',
        ]
        for row in valid_points:
            status_label = 'Pendente'
            if row.get('ok') is True:
                status_label = 'Aprovado'
            elif row.get('ok') is False:
                status_label = 'Reprovado'
            lines.append(
                f'Ponto {row["index"]} ({row["target"]}): '
                f'Cal={_format_num(row["calibration_m3h"], 4)} m3/h | '
                f'Ind={_format_num(row["indicated_m3h"], 4)} m3/h | '
                f'Ref={_format_num(row["reference_m3h"], 4)} m3/h'
            )
            lines.append(
                f'    Tend={_format_num(row["tendency_pct"], 4)}% | '
                f'U(e)={_format_num(row["uncertainty_pct"], 4)}% | '
                f'Soma={_format_num(row["combined_pct"], 4)}% | '
                f'Status={status_label}'
            )
        if not valid_points:
            lines.append('Nenhum ponto válido encontrado no certificado.')
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
    elif submission.is_truck_scale_form:
        valid_points = submission.truck_valid_points
        approved_points = sum(1 for row in valid_points if row.get('ok') is True)
        total_points = len(valid_points)
        certificate_summary_rows = [row for row in submission.truck_points if row.get('combined_kg') is not None]
        certificate_summary_kind = 'truck'
        lines = [
            f'{form_code} - {form_title}',
            f'Data da visita: {submission.execution_date}',
            f'OM: {submission.om_number}',
            f'Equipamento: {submission.equipment.tag} - {submission.equipment.description}',
            f'Local: {submission.location_snapshot}',
            f'Executor: {submission.executor_name}',
            f'Certificado: {Path(submission.truck_certificate_file.name).name if submission.truck_certificate_file else "-"}',
            f'Número do certificado: {submission.truck_certificate_number or "-"}',
            f'TAG no certificado: {submission.truck_tag_on_certificate or "-"}',
            f'Modelo da balança: {submission.truck_model or "-"}',
            f'Série: {submission.truck_serial_number or "-"}',
            f'Laboratório/fornecedor: {submission.truck_provider or "-"}',
            f'Data da calibração no certificado: {submission.truck_measurement_date or "-"}',
            f'Critério de aceitação ({acceptance_unit}): {_format_num(acceptance_limit, 1)}',
            f'U(e) declarada no certificado ({uncertainty_unit}): {_format_num(uncertainty_calc, 3)}',
            f'Maior erro absoluto ({acceptance_unit}): {_format_num(submission.truck_max_error_abs_kg, 3)}',
            f'Maior soma |erro| + U(e) ({acceptance_unit}): {_format_num(combined_value, 3)}',
            f'Pontos aprovados: {approved_points}/{total_points}',
            f'Status final: {combined_status}',
            '',
            'Pontos avaliados (carga/leitura/erro/U(e)):',
        ]
        for row in valid_points:
            status_label = 'Pendente'
            if row.get('ok') is True:
                status_label = 'Aprovado'
            elif row.get('ok') is False:
                status_label = 'Reprovado'
            lines.append(
                f'Ponto {row["index"]} ({row["label"]}): '
                f'Carga={_format_num(row["load_kg"], 3)} kg | '
                f'Leitura={_format_num(row["reading_kg"], 3)} kg | '
                f'Erro={_format_num(row["error_kg"], 3)} kg'
            )
            lines.append(
                f'    |Erro|={_format_num(row["error_abs_kg"], 3)} kg | '
                f'U(e)={_format_num(row["uncertainty_kg"], 3)} kg | '
                f'Soma={_format_num(row["combined_kg"], 3)} kg | '
                f'Status={status_label}'
            )
        if not valid_points:
            lines.append('Nenhum ponto válido encontrado no certificado.')
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
    elif submission.is_scanner_form:
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
    elif submission.is_flow_adjust_form:
        before_error = submission.flow_adjust_error_before_pct_auto
        final_error = submission.flow_adjust_final_error_pct
        lines = [
            f'{form_code} - {form_title}',
            f'Data da visita: {submission.execution_date}',
            f'OM: {submission.om_number}',
            f'Equipamento: {submission.equipment.tag} - {submission.equipment.description}',
            f'Local: {submission.location_snapshot}',
            f'Executor: {submission.executor_name}',
            f'Fase final considerada: {submission.flow_adjust_final_phase_label}',
            f'Critério de aceitação ({acceptance_unit}): {_format_num(acceptance_limit, 1)}',
            '',
            'Dados de processo:',
            f'Espessura 1/2/3/4 (mm): {_format_num(submission.flow_adjust_thickness_1_mm, 3)} / {_format_num(submission.flow_adjust_thickness_2_mm, 3)} / {_format_num(submission.flow_adjust_thickness_3_mm, 3)} / {_format_num(submission.flow_adjust_thickness_4_mm, 3)}',
            f'VM (média espessura) (mm): {_format_num(submission.flow_adjust_pipe_thickness_mean_mm, 3)}',
            f'Circunferência CI (mm): {_format_num(submission.flow_adjust_circumference_ci_mm, 3)}',
            f"POL' da tubulação (pol): {_format_num(submission.flow_adjust_pipe_nominal_in, 3)}",
            f'DE (diâmetro externo) (mm): {_format_num(submission.flow_adjust_external_diameter_mm, 3)} ({submission.flow_adjust_external_diameter_source_label})',
            f'Di (diâmetro interno) (mm): {_format_num(submission.flow_adjust_internal_diameter_mm, 3)}',
            '',
            'Verificação antes do ajuste:',
            f'TOTMV antes (m3): {_format_num(submission.flow_adjust_before_totmv_m3, 3)}',
            f'TOTSUP antes (m3): {_format_num(submission.flow_adjust_before_totsup_m3, 3)}',
            f'Tempo de medição antes (min): {_format_num(submission.flow_adjust_before_duration_min, 2)}',
            f'Erro antes ({acceptance_unit}): {_format_num(before_error, 3)}',
            f'Status erro antes: {_acceptance_label_for_value(before_error, acceptance_limit)}',
            '',
            'Verificação após ajuste:',
            f'TOTMV após ajuste (m3): {_format_num(submission.flow_adjust_after_totmv_m3, 3)}',
            f'TOTSUP após ajuste (m3): {_format_num(submission.flow_adjust_after_totsup_m3, 3)}',
            f'Tempo de medição após ajuste (min): {_format_num(submission.flow_adjust_after_duration_min, 2)}',
            f'Erro final ({acceptance_unit}): {_format_num(final_error, 3)}',
            f'Status erro final: {_acceptance_label_for_value(final_error, acceptance_limit)}',
            '',
            'Incerteza expandida:',
            f'u(CI) (mm): {_format_num(submission.flow_adjust_u_ci_mm_value, 3)}',
            f'u_inst_t (mm): {_format_num(submission.flow_adjust_u_inst_t_mm_value, 3)}',
            f'u(Δt) (s): {_format_num(submission.flow_adjust_u_delta_t_s_value, 3)}',
            f'u_repeat DUT (%): {_format_num(submission.flow_adjust_u_dut_repeat_pct_value, 3)}',
            f'u_res DUT (%): {_format_num(submission.flow_adjust_u_dut_res_pct_value, 3)}',
            f'k: {_format_num(submission.flow_adjust_k_factor_value, 3)}',
            f'u_rel geometria (%): {_format_num(submission.flow_adjust_u_rel_geom_pct, 3)}',
            f'u_rel tempo (%): {_format_num(submission.flow_adjust_u_rel_tempo_pct, 3)}',
            f'u_ref_total (%): {_format_num(submission.flow_adjust_u_ref_total_pct, 3)}',
            f'u_dut_total (%): {_format_num(submission.flow_adjust_u_dut_total_pct, 3)}',
            f'u_rel_r (%): {_format_num(submission.flow_adjust_u_rel_r_pct, 3)}',
            f'U(e) final ({uncertainty_unit}): {_format_num(submission.flow_adjust_u_expanded_pct, 3)}',
            '',
            f'Soma final |erro| + U(e) ({acceptance_unit}): {_format_num(combined_value, 3)}',
            f'Status final: {combined_status} (limite <= {_format_num(acceptance_limit, 1)}{acceptance_unit})',
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
    elif submission.is_density_form:
        before_rows = submission.density_before_rows
        after_rows = submission.density_after_rows
        lines = [
            f'{form_code} - {form_title}',
            f'Data da visita: {submission.execution_date}',
            f'OM: {submission.om_number}',
            f'Equipamento: {submission.equipment.tag} - {submission.equipment.description}',
            f'Local: {submission.location_snapshot}',
            f'Executor: {submission.executor_name}',
            f'Balança estática: {submission.density_scale_equipment.tag if submission.density_scale_equipment else "-"}',
            (
                'Aferidores selecionados: '
                f'{submission.density_standard_1.tag if submission.density_standard_1 else "-"} / '
                f'{submission.density_standard_2.tag if submission.density_standard_2 else "-"} / '
                f'{submission.density_standard_3.tag if submission.density_standard_3 else "-"}'
            ),
            '',
            'Checagem da balança (pré-requisito):',
            f'MAB (kg): {_format_num(submission.density_scale_mab_kg, 3)}',
            f'MIB (kg): {_format_num(submission.density_scale_mib_kg, 3)}',
            f'Erro da balança (%): {_format_num(submission.density_scale_error_pct, 3)}',
            f'Critério da balança (%): {_format_num(submission.density_scale_criterion_value, 1)}',
            f'Status da balança: {submission.density_scale_status_label}',
            f'u adicional por pesagem (kg): {_format_num(submission.density_scale_u_additional_kg_value, 4)}',
            '',
            'Verificação antes do ajuste:',
            f'MDA antes (g/cm³): {_format_num(submission.density_before_mda_gcm3, 4)}',
            f'MDS antes (g/cm³): {_format_num(submission.density_before_mds_gcm3, 4)}',
            f'Erro antes (%): {_format_num(submission.density_before_error_pct, 3)}',
            f'U(e) antes (%): {_format_num(submission.density_before_u_expanded_pct, 3)}',
            f'Margem antes |erro| + U(e) (%): {_format_num(submission.density_before_margin_pct, 3)}',
            f'Status antes: {submission.density_before_status_label}',
            '',
            'Verificação após ajuste:',
            f'MDA após (g/cm³): {_format_num(submission.density_after_mda_gcm3, 4)}',
            f'MDS após (g/cm³): {_format_num(submission.density_after_mds_gcm3, 4)}',
            f'Erro após (%): {_format_num(submission.density_after_error_pct, 3)}',
            f'U(e) após (%): {_format_num(submission.density_after_u_expanded_pct, 3)}',
            f'Margem após |erro| + U(e) (%): {_format_num(submission.density_after_margin_pct, 3)}',
            f'Status após: {submission.density_after_status_label}',
            '',
            'Resultado final:',
            f'Fase final considerada: {submission.density_final_phase_label}',
            f'Critério de aceitação ({acceptance_unit}): {_format_num(acceptance_limit, 2)}',
            f'Erro final ({acceptance_unit}): {_format_num(submission.density_final_error_pct, 3)}',
            f'U(e) final ({uncertainty_unit}): {_format_num(submission.density_final_u_expanded_pct, 3)}',
            f'Soma final |erro| + U(e) ({acceptance_unit}): {_format_num(submission.density_final_margin_pct, 3)}',
            f'Status final: {submission.acceptance_status_label}',
            '',
            'Parâmetros de incerteza:',
            f'Graduação do aferidor (L): {_format_num(submission.density_volume_graduation_l_value, 4)}',
            f'Resolução MDS (g/cm³): {_format_num(submission.density_mds_resolution_gcm3_value, 4)}',
            f'Fator k: {_format_num(submission.density_k_factor_value, 3)}',
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
            '',
            'Pontos antes (vazio/cheio/massa/volume/densidade/u_densidade):',
        ]
        for row in before_rows:
            lines.append(
                f'[{row["index"]}] {row["label"]}: '
                f'Vazio={_format_num(row["empty_kg"], 3)} kg | '
                f'Cheio={_format_num(row["full_kg"], 3)} kg | '
                f'Massa={_format_num(row["mass_kg"], 3)} kg | '
                f'Volume={_format_num(row["volume_l"], 3)} L | '
                f'Densidade={_format_num(row["density_gcm3"], 4)} g/cm³ | '
                f'u_dens={_format_num(row["u_density_gcm3"], 6)}'
            )
        lines.append('')
        lines.append('Pontos após (vazio/cheio/massa/volume/densidade/u_densidade):')
        for row in after_rows:
            lines.append(
                f'[{row["index"]}] {row["label"]}: '
                f'Vazio={_format_num(row["empty_kg"], 3)} kg | '
                f'Cheio={_format_num(row["full_kg"], 3)} kg | '
                f'Massa={_format_num(row["mass_kg"], 3)} kg | '
                f'Volume={_format_num(row["volume_l"], 3)} L | '
                f'Densidade={_format_num(row["density_gcm3"], 4)} g/cm³ | '
                f'u_dens={_format_num(row["u_density_gcm3"], 6)}'
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
    max_text_width = page_width - 80
    for raw_line in lines:
        wrapped_lines = simpleSplit(str(raw_line), 'Helvetica', 10, max_text_width) or ['']
        for line in wrapped_lines:
            if y < 60:
                pdf.showPage()
                y = _draw_pdf_header(pdf, page_width, page_height, ImageReader)
                pdf.setFont('Helvetica', 10)
            pdf.drawString(40, y, line)
            y -= line_height

    if certificate_summary_rows and certificate_summary_kind in {'flow', 'truck'}:
        table_rows = certificate_summary_rows
        if table_rows:
            table_title_h = 18
            table_header_h = 18
            table_row_h = 18
            table_gap_after = 14
            table_total_h = table_title_h + table_header_h + (len(table_rows) * table_row_h) + table_gap_after

            required_after = 0
            if include_signature:
                required_after += 108
            if y < (60 + table_total_h + required_after):
                pdf.showPage()
                y = _draw_pdf_header(pdf, page_width, page_height, ImageReader)
                pdf.setFont('Helvetica', 10)

            x0 = 40
            col_widths = [45, 85, 85, 85, 85, 130]  # total 515 (A4 com margem 40)
            if certificate_summary_kind == 'flow':
                headers = ['Ponto', '|Erro| (%)', 'U(e) (%)', 'Soma (%)', 'Limite (%)', 'Status']
            else:
                headers = ['Ponto', '|Erro| (kg)', 'U(e) (kg)', 'Soma (kg)', 'Limite (kg)', 'Status']

            pdf.setFont('Helvetica-Bold', 10)
            pdf.drawString(x0, y, 'Mini-tabela de fechamento por ponto')
            y -= table_title_h

            # Cabeçalho
            current_x = x0
            for idx, header in enumerate(headers):
                w = col_widths[idx]
                pdf.setFillColor(colors.HexColor('#f0ece2'))
                pdf.rect(current_x, y - table_header_h + 3, w, table_header_h, fill=1, stroke=1)
                pdf.setFillColor(colors.black)
                pdf.setFont('Helvetica-Bold', 8)
                pdf.drawCentredString(current_x + (w / 2), y - 9, header)
                current_x += w
            y -= table_header_h

            # Linhas
            for row in table_rows:
                status = 'Pendente'
                status_bg = colors.HexColor('#fff0c5')
                if row.get('ok') is True:
                    status = 'Aprovado'
                    status_bg = colors.HexColor('#d8f0df')
                elif row.get('ok') is False:
                    status = 'Reprovado'
                    status_bg = colors.HexColor('#ffdede')

                if certificate_summary_kind == 'flow':
                    data = [
                        str(row.get('index') or '-'),
                        _format_num(row.get('error_abs_pct'), 4),
                        _format_num(row.get('uncertainty_pct'), 4),
                        _format_num(row.get('combined_pct'), 4),
                        _format_num(acceptance_limit, 1),
                        status,
                    ]
                else:
                    data = [
                        str(row.get('index') or '-'),
                        _format_num(row.get('error_abs_kg'), 3),
                        _format_num(row.get('uncertainty_kg'), 3),
                        _format_num(row.get('combined_kg'), 3),
                        _format_num(acceptance_limit, 1),
                        status,
                    ]

                current_x = x0
                for idx, cell in enumerate(data):
                    w = col_widths[idx]
                    if idx == len(data) - 1:
                        pdf.setFillColor(status_bg)
                        pdf.rect(current_x, y - table_row_h + 3, w, table_row_h, fill=1, stroke=1)
                        pdf.setFillColor(colors.black)
                        pdf.setFont('Helvetica-Bold', 8)
                    else:
                        pdf.setFillColor(colors.white)
                        pdf.rect(current_x, y - table_row_h + 3, w, table_row_h, fill=1, stroke=1)
                        pdf.setFillColor(colors.black)
                        pdf.setFont('Helvetica', 8)
                    pdf.drawCentredString(current_x + (w / 2), y - 9, str(cell)[:24])
                    current_x += w
                y -= table_row_h

            y -= table_gap_after

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
    certificate_file_ref = submission.attached_certificate_file
    if not certificate_file_ref:
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

        with certificate_file_ref.open('rb') as certificate_file:
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
    if submission.attached_certificate_file:
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

