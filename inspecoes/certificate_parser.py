from __future__ import annotations

import io
import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from statistics import StatisticsError, stdev


def _normalize_ascii(text: str) -> str:
    text = unicodedata.normalize('NFKD', text or '')
    text = ''.join(ch for ch in text if not unicodedata.combining(ch))
    return text.upper()


def _to_decimal(token: str) -> Decimal | None:
    if token is None:
        return None
    cleaned = str(token).strip().replace('\u00a0', '')
    if not cleaned:
        return None

    cleaned = cleaned.replace(' ', '')
    cleaned = re.sub(r'[^0-9,.\-+]', '', cleaned)
    if not cleaned:
        return None

    if ',' in cleaned and '.' in cleaned:
        if cleaned.rfind(',') > cleaned.rfind('.'):
            cleaned = cleaned.replace('.', '').replace(',', '.')
        else:
            cleaned = cleaned.replace(',', '')
    elif ',' in cleaned:
        cleaned = cleaned.replace('.', '').replace(',', '.')
    else:
        parts = cleaned.split('.')
        if len(parts) > 2:
            cleaned = ''.join(parts[:-1]) + '.' + parts[-1]

    try:
        return Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None


def _parse_date(token: str) -> date | None:
    if not token:
        return None
    text = token.strip()
    match = re.search(r'(\d{2})[./-](\d{2})[./-](\d{4})', text)
    if not match:
        return None
    day, month, year = match.groups()
    try:
        return date(int(year), int(month), int(day))
    except ValueError:
        return None


def _extract_text_from_pdf_bytes(pdf_bytes: bytes) -> str:
    try:
        from pypdf import PdfReader
    except Exception as exc:
        raise RuntimeError(
            'Biblioteca pypdf não instalada no ambiente. Execute: pip install -r requirements.txt'
        ) from exc
    reader = PdfReader(io.BytesIO(pdf_bytes))
    chunks = []
    for page in reader.pages:
        chunks.append(page.extract_text() or '')
    return '\n'.join(chunks)


def _extract_linear_accuracy_points(text: str) -> tuple[list[dict], Decimal | None]:
    normalized = _normalize_ascii(text)
    start_idx = normalized.find('ACURACIA  DA MEDIDA LINEAR')
    if start_idx < 0:
        start_idx = normalized.find('ACURACIA DA MEDIDA LINEAR')
    end_idx = normalized.find('PRECISAO DE MEDICAO')
    if end_idx < 0:
        end_idx = normalized.find('4) PRECISAO')
    section = text[start_idx:end_idx] if start_idx >= 0 and end_idx > start_idx else text

    points: list[dict] = []
    default_fixed_mm: Decimal | None = None
    seen_pairs: set[tuple[str, str]] = set()

    line_pattern = re.compile(
        r'(?P<label>[A-Za-zÀ-ÿ0-9()°ºª?/\- .]+?)\s+'
        r'(?P<nom>\d+[.,]\d+)\s*m\s+'
        r'(?P<med>\d+[.,]\d+)\s*m\s+'
        r'(?P<ca>\d+[.,]?\d*)\s*mm',
        re.IGNORECASE,
    )

    for raw_line in section.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = line_pattern.search(line)
        if not match:
            continue
        nominal = _to_decimal(match.group('nom'))
        measured = _to_decimal(match.group('med'))
        fixed_mm = _to_decimal(match.group('ca'))
        if nominal is None or measured is None:
            continue
        pair_key = (str(nominal), str(measured))
        if pair_key in seen_pairs:
            continue
        seen_pairs.add(pair_key)
        label = re.sub(r'\([^)]*mm[^)]*\)', '', (match.group('label') or ''), flags=re.IGNORECASE)
        label = re.sub(r'\s+', ' ', label.strip())
        label = re.sub(r'Refletor\s+es', 'Refletores', label, flags=re.IGNORECASE)
        if len(label) > 60:
            label = label[:60]
        points.append(
            {
                'target': label or f'Refletor {len(points) + 1}',
                'nominal_m': nominal,
                'measured_m': measured,
            }
        )
        if default_fixed_mm is None and fixed_mm is not None:
            default_fixed_mm = fixed_mm
        if len(points) >= 6:
            break

    return points, default_fixed_mm


def _extract_precision_rep_mm(text: str) -> Decimal | None:
    normalized = _normalize_ascii(text)
    start_idx = normalized.find('PRECISAO DE MEDICAO')
    if start_idx < 0:
        return None
    snippet = text[start_idx : start_idx + 1200]
    match = re.search(r'(\d+[.,]\d+)\s*mm', snippet, re.IGNORECASE)
    if not match:
        return None
    return _to_decimal(match.group(1))


def _extract_residual_rep_mm(text: str) -> tuple[Decimal | None, int]:
    normalized = _normalize_ascii(text)
    start_idx = normalized.find('RESIDUOS  COM RELACAO A CADA ALVO')
    if start_idx < 0:
        start_idx = normalized.find('RESIDUOS COM RELACAO A CADA ALVO')
    if start_idx < 0:
        start_idx = normalized.find('RESIDUOS')
    if start_idx < 0:
        return None, 0

    section = text[start_idx : start_idx + 40000]
    delta_r_mm_values: list[float] = []

    # Linhas esperadas: índice + 8 valores numéricos (X, Y, Z, Range, ΔX, ΔY, ΔZ, ΔR).
    for raw_line in section.splitlines():
        line = raw_line.strip()
        if not line or not re.match(r'^\d+\s+', line):
            continue
        numeric_tokens = re.findall(r'[-+]?\d+[.,]\d+', line)
        if len(numeric_tokens) < 8:
            continue
        delta_r_m = _to_decimal(numeric_tokens[-1])
        if delta_r_m is None:
            continue
        delta_r_mm_values.append(float(delta_r_m * Decimal('1000')))

    if len(delta_r_mm_values) < 2:
        return None, len(delta_r_mm_values)

    try:
        sample_std_mm = stdev(delta_r_mm_values)
    except StatisticsError:
        return None, len(delta_r_mm_values)

    u_rep_mm = sample_std_mm / (len(delta_r_mm_values) ** 0.5)
    return Decimal(str(u_rep_mm)), len(delta_r_mm_values)


def _extract_metadata(text: str, filename: str = '') -> dict:
    data: dict = {}

    model_match = re.search(r'Modelo\s*:\s*(.+)', text, re.IGNORECASE)
    if model_match:
        model_text = model_match.group(1).split('\n', 1)[0]
        model_text = re.split(r'N\S*MERO\s+DE\s+S\S*RIE', _normalize_ascii(model_text), maxsplit=1, flags=re.IGNORECASE)[0]
        model_code_match = re.search(r'(VZ\s*[-]?\s*[0-9A-Za-z]+)', model_text, re.IGNORECASE)
        if model_code_match:
            data['scanner_model'] = re.sub(r'\s+', '', model_code_match.group(1)).upper()
        else:
            data['scanner_model'] = re.sub(r'\s+', ' ', model_text).strip(' .')

    serial_match = re.search(r'N[úu]mero\s+de\s+S[ée]rie\s*:\s*([A-Za-z0-9._\-]+)', text, re.IGNORECASE)
    if serial_match:
        data['scanner_serial_number'] = serial_match.group(1).strip()

    provider_match = re.search(r'Propriet[áa]rio\s*:\s*(.+)', text, re.IGNORECASE)
    if provider_match:
        provider = provider_match.group(1).split('\n', 1)[0]
        data['scanner_provider'] = re.sub(r'\s+', ' ', provider).strip(' .')

    date_match = re.search(r'Data\s+da\s+medi[çc][ãa]o\s*:\s*([0-9./-]{8,10})', text, re.IGNORECASE)
    if date_match:
        parsed = _parse_date(date_match.group(1))
        if parsed:
            data['scanner_measurement_date'] = parsed

    release_match = re.search(
        r'Data\s+(?:de\s+)?(?:emiss[aã]o|libera[çc][aã]o|issue)\s*:\s*([0-9./-]{8,10})',
        text,
        re.IGNORECASE,
    )
    if release_match:
        parsed = _parse_date(release_match.group(1))
        if parsed:
            data['scanner_release_date'] = parsed

    cert_number_match = re.search(r'Certificado\s*(?:N[oº°.]*)?\s*[:#]?\s*([A-Za-z0-9._\-]{4,})', text, re.IGNORECASE)
    if cert_number_match:
        value = cert_number_match.group(1).strip()
        if value.upper() not in {'DE', 'CALIBRACAO'}:
            data['scanner_certificate_number'] = value

    name = Path(filename or '').name
    if name:
        stem = Path(name).stem
        if 'scanner_model' not in data:
            model_name_match = re.search(r'_(VZ[0-9A-Za-z]+)', stem, re.IGNORECASE)
            if model_name_match:
                data['scanner_model'] = model_name_match.group(1).upper()
        if 'scanner_serial_number' not in data:
            serial_name_match = re.search(r'_([A-Z]\d{6,})$', stem, re.IGNORECASE)
            if serial_name_match:
                data['scanner_serial_number'] = serial_name_match.group(1).upper()
        if 'scanner_certificate_number' not in data and 'scanner_serial_number' in data:
            data['scanner_certificate_number'] = data['scanner_serial_number']

    return data


def parse_scanner_certificate(pdf_bytes: bytes, filename: str = '') -> dict:
    text = _extract_text_from_pdf_bytes(pdf_bytes)
    metadata = _extract_metadata(text, filename=filename)
    points, default_fixed_mm = _extract_linear_accuracy_points(text)
    residual_rep_mm, residual_count = _extract_residual_rep_mm(text)
    precision_rep_mm = _extract_precision_rep_mm(text)

    values: dict = {}
    values.update(metadata)

    if default_fixed_mm is not None:
        values['acceptance_criterion_pct'] = default_fixed_mm
        values['acceptance_criterion_unit'] = 'mm'
    if residual_rep_mm is not None:
        values['scanner_u_rep_mm'] = residual_rep_mm
    elif precision_rep_mm is not None:
        values['scanner_u_rep_mm'] = precision_rep_mm

    for index, point in enumerate(points, start=1):
        values[f'scanner_target_{index}'] = point['target']
        values[f'scanner_nominal_{index}_m'] = point['nominal_m']
        values[f'scanner_measured_{index}_m'] = point['measured_m']

    return {
        'values': values,
        'points_found': len(points),
        'default_fixed_mm': default_fixed_mm,
        'residual_count': residual_count,
        'residual_rep_mm': residual_rep_mm,
        'precision_rep_mm': precision_rep_mm,
        'raw_text': text,
    }


MONTH_MAP_PT = {
    'JAN': 1,
    'FEV': 2,
    'MAR': 3,
    'ABR': 4,
    'MAI': 5,
    'JUN': 6,
    'JUL': 7,
    'AGO': 8,
    'SET': 9,
    'OUT': 10,
    'NOV': 11,
    'DEZ': 12,
}


def _parse_date_flexible(token: str) -> date | None:
    parsed = _parse_date(token)
    if parsed:
        return parsed
    if not token:
        return None
    text = _normalize_ascii(token)
    match = re.search(r'(\d{2})[-/](JAN|FEV|MAR|ABR|MAI|JUN|JUL|AGO|SET|OUT|NOV|DEZ)[-/](\d{4})', text)
    if not match:
        return None
    day, month_abbr, year = match.groups()
    month = MONTH_MAP_PT.get(month_abbr)
    if not month:
        return None
    try:
        return date(int(year), int(month), int(day))
    except ValueError:
        return None


def _extract_flow_metadata(text: str, filename: str = '') -> dict:
    data: dict = {}
    normalized = _normalize_ascii(text)

    cert_match = re.search(
        r'NUMERO\s+DO\s+CERTIFICADO\s*:\s*([A-Z0-9._/-]+?)(?=MEDIDOR|SERVICO|CERTIFICADO|\s|$)',
        normalized,
    )
    if cert_match:
        data['flow_certificate_number'] = cert_match.group(1).strip()

    tag_match = re.search(r'TAG\s+DO\s+MEDIDOR\s*:\s*([A-Z0-9._/-]+)', normalized)
    if tag_match:
        data['flow_tag_on_certificate'] = tag_match.group(1).strip()

    model_meter_match = re.search(
        r'MODELO\s+DO\s+MEDIDOR\s*:\s*([A-Z0-9._/-]+)\s+S\w*RIE\s*:\s*([A-Z0-9._/-]+)',
        normalized,
    )
    if model_meter_match:
        data['flow_meter_model'] = model_meter_match.group(1).strip()
        data['flow_meter_serial_number'] = model_meter_match.group(2).strip()

    model_converter_match = re.search(
        r'MODELO\s+DO\s+CONVERSOR\s*:\s*([A-Z0-9._/-]+)\s+S\w*RIE\s*:\s*([A-Z0-9._/-]+)',
        normalized,
    )
    if model_converter_match:
        data['flow_converter_model'] = model_converter_match.group(1).strip()
        data['flow_converter_serial_number'] = model_converter_match.group(2).strip()

    range_match = re.search(
        r'FAIXA\s+CALIBRADA\s*:\s*\(\s*([0-9.,]+)\s+A\s+([0-9.,]+)\s*\)\s*M',
        normalized,
    )
    if range_match:
        data['flow_calibration_range_min_m3h'] = _to_decimal(range_match.group(1))
        data['flow_calibration_range_max_m3h'] = _to_decimal(range_match.group(2))

    calibration_date_match = re.search(
        r'DATA\s+DA\s+CALIBRACAO\s*:\s*([0-9]{2}[-/][A-Z]{3}[-/][0-9]{4}|[0-9./-]{8,10})',
        normalized,
    )
    if calibration_date_match:
        parsed = _parse_date_flexible(calibration_date_match.group(1))
        if parsed:
            data['flow_measurement_date'] = parsed

    release_date_match = re.search(
        r'DATA\s+DA\s+EMISSAO\s+DO\s+CERTIFICADO\s*:\s*([0-9]{2}[-/][A-Z]{3}[-/][0-9]{4}|[0-9./-]{8,10})',
        normalized,
    )
    if release_date_match:
        parsed = _parse_date_flexible(release_date_match.group(1))
        if parsed:
            data['flow_release_date'] = parsed

    provider_match = re.search(r'LABORATORIO\s+DA\s+([A-Z0-9 ._-]+)', normalized)
    if provider_match:
        provider = re.sub(r'\s+', ' ', provider_match.group(1)).strip(' .')
        if provider:
            data['flow_provider'] = provider.title()

    if 'flow_provider' not in data:
        data['flow_provider'] = 'Emerson Process Management Ltda.'

    name = Path(filename or '').name
    if name and 'flow_tag_on_certificate' not in data:
        stem = _normalize_ascii(Path(name).stem)
        tag_name_match = re.search(r'(FIT[-_][0-9A-Z-]+)', stem)
        if tag_name_match:
            data['flow_tag_on_certificate'] = tag_name_match.group(1).replace('_', '-')

    return data


def _extract_flow_points(text: str) -> list[dict]:
    points: list[dict] = []
    seen_rows: set[tuple[str, str, str, str, str]] = set()

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or not re.match(r'^\d+[.,]\d+', line):
            continue

        numeric_tokens = re.findall(r'[+-]?\d+[.,]\d+', line)
        if len(numeric_tokens) < 7:
            continue

        calibration_m3h = _to_decimal(numeric_tokens[0])
        indicated_m3h = _to_decimal(numeric_tokens[1])
        reference_m3h = _to_decimal(numeric_tokens[2])
        tendency_pct = _to_decimal(numeric_tokens[3])
        uncertainty_pct = _to_decimal(numeric_tokens[5])
        k_factor = _to_decimal(numeric_tokens[6])

        if calibration_m3h is None or indicated_m3h is None or reference_m3h is None:
            continue
        if tendency_pct is None or uncertainty_pct is None:
            continue

        row_key = (
            str(calibration_m3h),
            str(indicated_m3h),
            str(reference_m3h),
            str(tendency_pct),
            str(uncertainty_pct),
        )
        if row_key in seen_rows:
            continue
        seen_rows.add(row_key)

        points.append(
            {
                'calibration_m3h': calibration_m3h,
                'indicated_m3h': indicated_m3h,
                'reference_m3h': reference_m3h,
                'tendency_pct': tendency_pct,
                'uncertainty_pct': uncertainty_pct,
                'k_factor': k_factor,
            }
        )
        if len(points) >= 6:
            break

    return points


def parse_flow_certificate(pdf_bytes: bytes, filename: str = '') -> dict:
    text = _extract_text_from_pdf_bytes(pdf_bytes)
    metadata = _extract_flow_metadata(text, filename=filename)
    points = _extract_flow_points(text)

    values: dict = {}
    values.update(metadata)
    for index, point in enumerate(points, start=1):
        values[f'flow_point_label_{index}'] = f'Ponto {index}'
        values[f'flow_calibration_{index}_m3h'] = point['calibration_m3h']
        values[f'flow_indicated_{index}_m3h'] = point['indicated_m3h']
        values[f'flow_reference_{index}_m3h'] = point['reference_m3h']
        values[f'flow_tendency_{index}_pct'] = point['tendency_pct']
        values[f'flow_uncertainty_{index}_pct'] = point['uncertainty_pct']
        values[f'flow_k_{index}'] = point['k_factor']

    return {
        'values': values,
        'points_found': len(points),
        'raw_text': text,
    }


def _extract_truck_scale_metadata(text: str, filename: str = '') -> dict:
    data: dict = {}
    normalized = _normalize_ascii(text)

    cert_match = re.search(r'\bN[º°O]?\s*([A-Z0-9]+/[0-9-]+)', normalized)
    if cert_match:
        data['truck_certificate_number'] = cert_match.group(1).strip()

    tag_match = re.search(
        r'PATRIMONIO\s+IDENT\.?\s*TECNICA\s*\(TAG\)\s*\|?\s*([A-Z0-9._/-]+?)(?=SERIE|ENDERECO|MODELO|FABRICANTE|CLIENTE|$)',
        normalized,
    )
    if tag_match:
        data['truck_tag_on_certificate'] = tag_match.group(1).strip()

    model_match = re.search(
        r'MODELO\s*\|?\s*([A-Z0-9._/-]+?)(?=FABRICANTE|CLIENTE|$)',
        normalized,
    )
    if model_match:
        data['truck_model'] = model_match.group(1).strip()

    serial_match = re.search(
        r'SERIE\s*\|?\s*([A-Z0-9._/-]+?)(?=ENDERECO|MODELO|FABRICANTE|CLIENTE|$)',
        normalized,
    )
    if serial_match:
        data['truck_serial_number'] = serial_match.group(1).strip()

    provider_match = re.search(r'FABRICANTE\s*\|?\s*([A-Z0-9 ._-]+?)(?=CLIENTE|CERTIFICADO|$)', normalized)
    if provider_match:
        provider = re.sub(r'\s+', ' ', provider_match.group(1)).strip(' .')
        if provider:
            data['truck_provider'] = provider.title()

    measurement_date_match = re.search(
        r'DATA\s+DE\s+CALIBRACAO\s*\|?\s*([0-9]{2}[-/][A-Z]{3}[-/][0-9]{4}|[0-9./-]{8,10})',
        normalized,
    )
    if measurement_date_match:
        parsed = _parse_date_flexible(measurement_date_match.group(1))
        if parsed:
            data['truck_measurement_date'] = parsed

    uncertainty_match = re.search(
        r'INCERTEZA\s+EXPANDIDA\s*:\s*[±+\-]?\s*([0-9.,]+)\s*KG',
        normalized,
    )
    if uncertainty_match:
        data['truck_uncertainty_declared_kg'] = _to_decimal(uncertainty_match.group(1))

    k_match = re.search(r'K\s*=\s*([0-9.,]+)', normalized)
    if k_match:
        data['truck_k_factor'] = _to_decimal(k_match.group(1))

    name = Path(filename or '').name
    if name and 'truck_tag_on_certificate' not in data:
        stem = _normalize_ascii(Path(name).stem)
        tag_name_match = re.search(r'(BL[-_][0-9A-Z-]+)', stem)
        if tag_name_match:
            data['truck_tag_on_certificate'] = tag_name_match.group(1).replace('_', '-')

    return data


def _extract_truck_scale_points(text: str) -> tuple[list[dict], str]:
    normalized = _normalize_ascii(text)
    start_idx = normalized.find('TESTE DE PESAGEM')
    section = text[start_idx:] if start_idx >= 0 else text
    section_normalized = _normalize_ascii(section)

    phase = 'DEPOIS'
    phase_idx = section_normalized.find('DEPOIS')
    if phase_idx >= 0:
        section = section[phase_idx:]
        section_normalized = section_normalized[phase_idx:]
    else:
        phase = 'ANTES'
        before_idx = section_normalized.find('ANTES')
        if before_idx >= 0:
            section = section[before_idx:]
            section_normalized = section_normalized[before_idx:]

    end_markers = ['RESOLUCAO', 'TOLERANCIAS', 'INSTALACOES', 'METODO', 'INSTRUCAO DE TRABALHO']
    end_positions = [section_normalized.find(marker) for marker in end_markers if section_normalized.find(marker) >= 0]
    if end_positions:
        section = section[: min(end_positions)]

    points: list[dict] = []
    seen_rows: set[tuple[str, str, str]] = set()
    point_pattern = re.compile(
        r'([+-]?[0-9][0-9.,]*)\s*kg\s+([+-]?[0-9][0-9.,]*)\s*kg\s+([+-]?[0-9][0-9.,]*)\s*kg',
        re.IGNORECASE,
    )

    raw_points: list[tuple[Decimal, Decimal, Decimal]] = []
    for match in point_pattern.finditer(section):
        load_kg = _to_decimal(match.group(1))
        reading_kg = _to_decimal(match.group(2))
        error_kg = _to_decimal(match.group(3))
        if load_kg is None or reading_kg is None or error_kg is None:
            continue
        raw_points.append((load_kg, reading_kg, error_kg))

    filtered_points = [
        row
        for row in raw_points
        if not (row[0] == 0 and row[1] == 0 and row[2] == 0)
    ]
    candidate_points = filtered_points if filtered_points else raw_points

    for load_kg, reading_kg, error_kg in candidate_points:
        row_key = (str(load_kg), str(reading_kg), str(error_kg))
        if row_key in seen_rows:
            continue
        seen_rows.add(row_key)
        points.append(
            {
                'load_kg': load_kg,
                'reading_kg': reading_kg,
                'error_kg': error_kg,
            }
        )
        if len(points) >= 6:
            break

    return points, phase


def parse_truck_scale_certificate(pdf_bytes: bytes, filename: str = '') -> dict:
    text = _extract_text_from_pdf_bytes(pdf_bytes)
    metadata = _extract_truck_scale_metadata(text, filename=filename)
    points, phase = _extract_truck_scale_points(text)

    values: dict = {}
    values.update(metadata)
    for index, point in enumerate(points, start=1):
        values[f'truck_point_label_{index}'] = f'Ponto {index}'
        values[f'truck_load_{index}_kg'] = point['load_kg']
        values[f'truck_reading_{index}_kg'] = point['reading_kg']
        values[f'truck_error_{index}_kg'] = point['error_kg']

    return {
        'values': values,
        'points_found': len(points),
        'phase_used': phase,
        'raw_text': text,
    }
