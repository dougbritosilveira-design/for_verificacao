from __future__ import annotations

import io
import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from pypdf import PdfReader


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
    precision_rep_mm = _extract_precision_rep_mm(text)

    values: dict = {}
    values.update(metadata)

    if default_fixed_mm is not None:
        values['acceptance_criterion_pct'] = default_fixed_mm
        values['acceptance_criterion_unit'] = 'mm'
    if precision_rep_mm is not None:
        values['scanner_u_rep_mm'] = precision_rep_mm

    for index, point in enumerate(points, start=1):
        values[f'scanner_target_{index}'] = point['target']
        values[f'scanner_nominal_{index}_m'] = point['nominal_m']
        values[f'scanner_measured_{index}_m'] = point['measured_m']

    return {
        'values': values,
        'points_found': len(points),
        'default_fixed_mm': default_fixed_mm,
        'precision_rep_mm': precision_rep_mm,
        'raw_text': text,
    }
