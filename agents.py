"""Independent retrieval agents and deterministic synthesis of research records."""
from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

EPMC_URL = 'https://www.ebi.ac.uk/europepmc/webservices/rest/search'
TRIALS_URL = 'https://clinicaltrials.gov/api/v2/studies'
NCT_PATTERN = re.compile(r'\bNCT\d{8}\b', re.IGNORECASE)
CACHE_DIR = Path(__file__).resolve().parent / '.cache'
CACHE_MAX_AGE = timedelta(days=14)
TRIAL_DATE_FIELDS = {'StartDate', 'StudyFirstPostDate'}
# The keys are internal UI values. API clauses are fixed/whitelisted, not user-built.
PUBLICATION_TYPE_QUERIES = {
    'all': '',
    'review': 'PUB_TYPE:review',
    'clinical_trial': 'PUB_TYPE:"clinical trial"',
    'randomized_trial': 'PUB_TYPE:"randomized controlled trial"',
    'meta_analysis': 'PUB_TYPE:"meta-analysis"',
}
TRIAL_PHASES = {'EARLY_PHASE1', 'PHASE1', 'PHASE2', 'PHASE3', 'PHASE4', 'NA'}
TRIAL_STATUSES = {
    'COMPLETED', 'RECRUITING', 'NOT_YET_RECRUITING', 'ACTIVE_NOT_RECRUITING',
    'ENROLLING_BY_INVITATION', 'TERMINATED', 'SUSPENDED', 'WITHDRAWN', 'UNKNOWN',
    'AVAILABLE', 'NO_LONGER_AVAILABLE', 'APPROVED_FOR_MARKETING', 'WITHHELD',
}
RESULTS_FILTERS = {'all', 'with', 'without'}


def _intervention_phrase(value: str) -> str:
    """A quoted literal term, so drug names cannot turn into query operators."""
    term = ' '.join(value.split())
    if len(term) > 150 or any(c in term for c in ('\"', '\\')):
        raise ValueError('Укажите название препарата без кавычек или обратных слэшей (до 150 символов).')
    return f'"{term}"' if term else ''




def _normalize_period(start_date: date | str | None,
                      end_date: date | str | None) -> tuple[str | None, str | None]:
    """Validate inclusive date limits for both API queries."""
    if start_date is None and end_date is None:
        return None, None
    if start_date is None or end_date is None:
        raise ValueError('Укажите обе границы периода: «С» и «По».')
    try:
        start = date.fromisoformat(str(start_date))
        end = date.fromisoformat(str(end_date))
    except ValueError as exc:
        raise ValueError('Неверная дата: ожидается формат ГГГГ-ММ-ДД.') from exc
    if start > end:
        raise ValueError('Начальная дата не должна быть позже конечной.')
    return start.isoformat(), end.isoformat()



@dataclass
class PublicationsResult:
    records: list[dict]
    mode: str                 # 'core' or 'lite'
    retrieved_at_utc: str
    from_cache: bool = False
    note: str = ''


def _session() -> requests.Session:
    session = requests.Session()
    # One modest retry: avoid hammering an overloaded external service.
    retry = Retry(
        total=1,
        backoff_factor=1.0,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=['GET'],
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    session.mount('https://', HTTPAdapter(max_retries=retry))
    session.headers.update({
        'User-Agent': 'MedicalResearchExplorer/1.1 (academic demo)',
        'Accept': 'application/json',
    })
    return session


def _get_json(url: str, params: dict[str, Any]) -> dict:
    with _session() as session:
        response = session.get(url, params=params, timeout=(5, 20))
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError('API вернул JSON неверного формата')
    return payload


def _plain(value: Any) -> str:
    if not value:
        return ''
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', str(value))).strip()


def _cache_path(query: str, limit: int) -> Path:
    key = json.dumps({'query': query.strip(), 'limit': limit}, sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha256(key.encode('utf-8')).hexdigest()[:24]
    return CACHE_DIR / f'europepmc_{digest}.json'


def _save_epmc_cache(query: str, limit: int, mode: str, payload: dict, timestamp: str) -> None:
    try:
        path = _cache_path(query, limit)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps({
            'retrieved_at_utc': timestamp, 'mode': mode, 'payload': payload,
        }, ensure_ascii=False), encoding='utf-8')
        os.replace(temp, path)
    except OSError:
        # A read-only hosting filesystem must not break live data retrieval.
        pass


def _load_epmc_cache(query: str, limit: int) -> dict | None:
    try:
        entry = json.loads(_cache_path(query, limit).read_text(encoding='utf-8'))
        collected = datetime.fromisoformat(entry['retrieved_at_utc'])
        if collected.tzinfo is None:
            return None
        if not (timedelta(0) <= datetime.now(timezone.utc) - collected <= CACHE_MAX_AGE):
            return None
        if entry['mode'] not in ('core', 'lite') or not isinstance(entry['payload']['resultList']['result'], list):
            return None
        return entry
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _fetch_epmc(query: str, limit: int, mode: str) -> dict:
    # Relevance is the official default: do NOT send undocumented sort=RELEVANCE.
    payload = _get_json(EPMC_URL, {
        'query': query, 'format': 'json', 'resultType': mode, 'pageSize': limit,
    })
    # Europe PMC sometimes responded HTTP 200 with only {'version': '...'}.
    # Treat that as a failed request, not as zero publications.
    result_list = payload.get('resultList')
    if not isinstance(result_list, dict) or not isinstance(result_list.get('result'), list):
        raise ValueError('Europe PMC прислал неполный ответ без списка результатов')
    return payload


def _parse_publications(payload: dict) -> list[dict]:
    """Normalize Europe PMC records for both the main and related-paper searches."""
    results = []
    seen = set()
    for item in payload['resultList']['result']:
        if not isinstance(item, dict):
            continue
        doi = str(item.get('doi') or '').strip()
        publication_id = str(item.get('id') or '').strip()
        source = str(item.get('source') or 'MED')
        pmid = str(item.get('pmid') or (publication_id if source == 'MED' else '')).strip()
        key = doi.lower() or (f'pmid:{pmid}' if pmid else f'{source}:{publication_id}')
        if key in seen:
            continue
        seen.add(key)
        title = _plain(item.get('title'))
        abstract = _plain(item.get('abstractText'))
        journal = (item.get('journalInfo') or {}).get('journal') or {}
        fallback_url = f'https://europepmc.org/article/{source}/{publication_id}' if publication_id else ''
        results.append({
            'title': title,
            'year': str(item.get('pubYear') or ''),
            'authors': _plain(item.get('authorString')),
            'journal': _plain(item.get('journalTitle') or journal.get('title')),
            'abstract': abstract,
            'doi': doi,
            'id': publication_id,
            'pmid': pmid,
            'url': f'https://doi.org/{doi}' if doi else fallback_url,
            'nct_ids': sorted({code.upper() for code in NCT_PATTERN.findall(abstract + ' ' + title)}),
        })
    return results


def publication_agent(query: str, limit: int,
                      start_date: date | str | None = None,
                      end_date: date | str | None = None,
                      intervention: str = '',
                      publication_type: str = 'all') -> PublicationsResult:
    """Try full metadata, then lightweight metadata, then a real local snapshot.

    The cache contains only previously successful responses from Europe PMC.
    It never fabricates publications and never silently presents cached data as live.
    """
    start, end = _normalize_period(start_date, end_date)
    if publication_type not in PUBLICATION_TYPE_QUERIES:
        raise ValueError('Неизвестный тип научной публикации.')
    phrase = _intervention_phrase(intervention)
    # All filters are part of the API query, before pageSize. The cache key
    # includes the fully assembled query: different filters cannot collide.
    clauses = [f'({query})']
    if phrase:
        clauses.append(phrase)
    if PUBLICATION_TYPE_QUERIES[publication_type]:
        clauses.append(PUBLICATION_TYPE_QUERIES[publication_type])
    if start:
        clauses.append(f'FIRST_PDATE:[{start} TO {end}]')
    effective_query = ' AND '.join(clauses) if len(clauses) > 1 else query
    failure: Exception | None = None
    payload = None
    mode = 'core'
    cache_entry = None

    for candidate in ('core', 'lite'):
        try:
            payload = _fetch_epmc(effective_query, limit, candidate)
            mode = candidate
            break
        except requests.HTTPError as exc:
            failure = exc
            # Bad request / authorization is not fixed by choosing a lighter result.
            status = exc.response.status_code if exc.response is not None else None
            if status is not None and status < 500 and status != 429:
                raise
        except (requests.RequestException, ValueError) as exc:
            failure = exc

    if payload is None:
        cache_entry = _load_epmc_cache(effective_query, limit)
        if cache_entry is None:
            assert failure is not None
            raise failure
        payload = cache_entry['payload']
        mode = cache_entry['mode']

    from_cache = cache_entry is not None
    timestamp = (cache_entry['retrieved_at_utc'] if from_cache
                 else datetime.now(timezone.utc).isoformat())
    if not from_cache:
        _save_epmc_cache(effective_query, limit, mode, payload, timestamp)

    note = ''
    if from_cache:
        note = f'Europe PMC временно недоступен. Показаны сохранённые данные от {timestamp} (UTC), а не новая выдача.'
    elif mode == 'lite':
        note = 'Europe PMC: полная выдача недоступна, использован облегчённый режим (аннотации могут отсутствовать).'

    return PublicationsResult(_parse_publications(payload), mode, timestamp, from_cache, note)


def trial_agent(query: str, limit: int,
                start_date: date | str | None = None,
                end_date: date | str | None = None,
                trial_date_field: str = 'StartDate',
                intervention: str = '',
                trial_status: str = '',
                trial_phase: str = '',
                results_filter: str = 'all') -> list[dict]:
    start, end = _normalize_period(start_date, end_date)
    if trial_date_field not in TRIAL_DATE_FIELDS:
        raise ValueError('Неизвестное поле даты для ClinicalTrials.gov.')
    if trial_status and trial_status not in TRIAL_STATUSES:
        raise ValueError('Неизвестный статус клинического испытания.')
    if trial_phase and trial_phase not in TRIAL_PHASES:
        raise ValueError('Неизвестная фаза клинического испытания.')
    if results_filter not in RESULTS_FILTERS:
        raise ValueError('Неизвестный фильтр наличия результатов.')
    _intervention_phrase(intervention)  # validate before calling external API
    params = {
        'query.term': query, 'pageSize': limit, 'format': 'json',
        'countTotal': 'true',
    }
    if intervention.strip():
        params['query.intr'] = intervention.strip()
    if trial_status:
        params['filter.overallStatus'] = trial_status
    # Phase, dates and posted results all use the API's advanced query.
    # Combining in one parameter avoids one criterion silently replacing another.
    advanced = []
    if start:
        advanced.append(f'AREA[{trial_date_field}]RANGE[{start},{end}]')
    if trial_phase:
        advanced.append(f'AREA[Phase]{trial_phase}')
    if results_filter != 'all':
        advanced.append(f'AREA[HasResults]{"true" if results_filter == "with" else "false"}')
    if advanced:
        params['filter.advanced'] = ' AND '.join(advanced)
    payload = _get_json(TRIALS_URL, params)
    results = []
    for item in payload.get('studies', []):
        p = item.get('protocolSection') or {}
        ident = p.get('identificationModule') or {}
        status = p.get('statusModule') or {}
        design = p.get('designModule') or {}
        desc = p.get('descriptionModule') or {}
        conditions = (p.get('conditionsModule') or {}).get('conditions') or []
        intervention_items = (p.get('armsInterventionsModule') or {}).get('interventions') or []
        interventions = ', '.join(_plain(value.get('name')) for value in intervention_items
                                  if isinstance(value, dict) and value.get('name'))
        nct = ident.get('nctId', '')
        raw_references = (p.get('referencesModule') or {}).get('references') or []
        references = []
        for ref in raw_references:
            if not isinstance(ref, dict):
                continue
            pmid = str(ref.get('pmid') or '').strip()
            if pmid and pmid.isdigit():
                references.append({
                    'pmid': pmid,
                    'type': str(ref.get('type') or 'UNKNOWN').upper(),
                    'citation': _plain(ref.get('citation')),
                })
        results.append({
            'nct_id': nct,
            'title': _plain(ident.get('briefTitle')),
            'status': status.get('overallStatus', 'UNKNOWN'),
            'phase': ', '.join(design.get('phases') or []),
            'study_type': design.get('studyType') or '',
            'references': references,
            'start_date': (status.get('startDateStruct') or {}).get('date', ''),
            'first_posted_date': (status.get('studyFirstPostDateStruct') or {}).get('date', ''),
            'conditions': ', '.join(conditions),
            'interventions': interventions,
            'summary': _plain(desc.get('briefSummary')),
            'has_results': bool(item.get('hasResults')),
            'url': f'https://clinicaltrials.gov/study/{nct}' if nct else '',
        })
    return results


STATUS_LABELS = {
    'COMPLETED': 'Завершено',
    'RECRUITING': 'Набор участников',
    'NOT_YET_RECRUITING': 'Набор ещё не начат',
    'ACTIVE_NOT_RECRUITING': 'Активно, без набора',
    'ENROLLING_BY_INVITATION': 'Набор по приглашению',
    'TERMINATED': 'Прекращено досрочно',
    'SUSPENDED': 'Приостановлено',
    'WITHDRAWN': 'Отозвано до начала',
    'UNKNOWN': 'Статус неизвестен',
    'AVAILABLE': 'Доступно',
    'NO_LONGER_AVAILABLE': 'Более недоступно',
    'APPROVED_FOR_MARKETING': 'Одобрено для применения',
    'WITHHELD': 'Данные пока не раскрыты',
}
PHASE_LABELS = {
    'EARLY_PHASE1': 'Ранняя фаза I',
    'PHASE1': 'Фаза I',
    'PHASE2': 'Фаза II',
    'PHASE3': 'Фаза III',
    'PHASE4': 'Фаза IV',
    'NA': 'Не применяется',
    'NOT_APPLICABLE': 'Не применяется',
    'UNKNOWN': 'Не указана',
}


def _phase_label(value: str) -> str:
    if not value:
        return 'Не указана'
    parts = [p.strip().upper() for p in value.split(',') if p.strip()]
    # An explicit NA is different from a missing phase.
    if not parts:
        return 'Не указана'
    return ' + '.join(PHASE_LABELS.get(p, p) for p in parts)


def _build_matches(publications: list[dict], trials: list[dict]) -> tuple[list[dict], list[dict]]:
    """Join by explicit NCT mentions or trial-reference PMIDs.

    A BACKGROUND reference is kept separate: it does not establish that the
    publication reports results of the registered trial.
    """
    trials_by_id = {t['nct_id'].upper(): t for t in trials if t.get('nct_id')}
    direct: dict[tuple[str, str], dict] = {}
    background: dict[tuple[str, str], dict] = {}

    def add(pub: dict, trial: dict, method: str, target: dict, kind: str) -> None:
        nct = trial['nct_id'].upper()
        pub_id = pub.get('pmid') or pub.get('doi') or pub.get('id') or pub.get('url') or pub.get('title')
        key = (nct, str(pub_id))
        row = target.setdefault(key, {
            'nct_id': nct,
            'trial_title': trial.get('title', ''),
            'publication_title': pub.get('title', ''),
            'publication_url': pub.get('url', ''),
            'trial_url': trial.get('url', ''),
            'pmid': pub.get('pmid', ''),
            'relation': kind,
            'methods': [],
        })
        if method not in row['methods']:
            row['methods'].append(method)

    for pub in publications:
        pmid = str(pub.get('pmid') or '')
        nct_ids = pub.get('nct_ids') or []
        for nct in nct_ids:
            trial = trials_by_id.get(str(nct).upper())
            if trial:
                add(pub, trial, 'Упоминание NCT ID', direct, 'Упоминание испытания')
        if not pmid:
            continue
        for trial in trials:
            for ref in trial.get('references') or []:
                if ref.get('pmid') != pmid:
                    continue
                category = (ref.get('type') or '').upper()
                if category in ('RESULT', 'DERIVED'):
                    add(pub, trial, 'PMID в реестре (' + category + ')', direct, 'Публикация указана в реестре')
                elif category == 'BACKGROUND':
                    add(pub, trial, 'PMID в фоновых источниках', background, 'Фоновая литература')
    # If a direct link was found, suppress its duplicate in background links.
    for key in direct:
        background.pop(key, None)
    return list(direct.values()), list(background.values())


def analysis_agent(query: str, publications: list[dict], trials: list[dict],
                   errors: dict[str, str], source_info: dict | None = None,
                   related_publications: list[dict] | None = None) -> dict:
    """Aggregate *only* retrieved records, with explicit linkage provenance."""
    related_publications = related_publications or []
    status_counts = Counter(STATUS_LABELS.get(t.get('status') or 'UNKNOWN', t.get('status') or 'Неизвестно') for t in trials)
    year_counts = Counter(str(p['year']) for p in publications if p.get('year'))
    phase_counts = Counter(_phase_label(t.get('phase') or '') for t in trials)
    unique_pubs = list(publications)
    existing_ids = {p.get('pmid') for p in publications if p.get('pmid')}
    unique_pubs.extend(p for p in related_publications if not p.get('pmid') or p.get('pmid') not in existing_ids)
    matches, background_matches = _build_matches(unique_pubs, trials)
    counts = {
        'publications': len(publications),
        'trials': len(trials),
        'trials_with_results': sum(bool(t.get('has_results')) for t in trials),
        'completed_trials': sum(t.get('status') == 'COMPLETED' for t in trials),
        'publications_with_abstract': sum(bool(p.get('abstract')) for p in publications),
        'related_publications': len(related_publications),
        'trials_with_references': sum(bool(t.get('references')) for t in trials),
    }
    highlights = []
    if trials:
        highlights.append(f"Из {len(trials)} выбранных испытаний завершено {counts['completed_trials']} ({counts['completed_trials']/len(trials):.0%}); "
                          f"результаты внесены в реестр для {counts['trials_with_results']} ({counts['trials_with_results']/len(trials):.0%}).")
        highlights.append(f"У {counts['trials_with_references']} испытаний есть ссылки на публикации с PMID в реестре.")
    if publications:
        highlights.append(f"Из {len(publications)} найденных публикаций аннотация доступна для {counts['publications_with_abstract']}.")
    if matches:
        highlights.append(f'Найдено {len(matches)} проверяемых связей по NCT ID или PMID, с переходом к обоим первоисточникам.')
    elif publications and trials:
        highlights.append('В выбранных записях подтверждённых пересечений не найдено; это не означает, что они отсутствуют в базах целиком.')

    return {
        'query': query,
        'collected_at_utc': datetime.now(timezone.utc).isoformat(),
        'counts': counts,
        'status_counts': dict(status_counts.most_common()),
        'year_counts': dict(sorted(year_counts.items())),
        'phase_counts': dict(phase_counts.most_common()),
        'matches': matches,
        'background_matches': background_matches,
        'related_publications': related_publications,
        'highlights': highlights,
        'publications': publications,
        'trials': trials,
        'errors': errors,
        'source_info': source_info or {},
        'limitations': [
            'Подсчитаны только загруженные записи, не все исследования по теме; выдача по релевантности не отражает тенденции по годам.',
            'При многофазном испытании комбинация фаз показана как одна категория, чтобы не удваивать число испытаний.',
            'Связи по PMID проверяются для ссылок типа RESULT/DERIVED; BACKGROUND показаны отдельно как фоновая литература.',
            'Упоминание NCT ID в статье не доказывает, что она сообщает результаты именно этого испытания.',
            'Наличие испытания или опубликованной статьи не доказывает эффективность или безопасность лечения.',
            'Флаг «есть результаты» означает наличие данных в ClinicalTrials.gov, а не наличие публикаций где-либо ещё.',
        ],
    }


def _concise_error(exc: Exception) -> str:
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        status = exc.response.status_code
        if status in (429, 500, 502, 503, 504):
            return f'HTTP {status}: сервис временно недоступен или перегружен. Повторите запрос позже.'
        return f'HTTP {status}: сервер отклонил запрос.'
    if isinstance(exc, requests.Timeout):
        return 'Превышено время ожидания ответа сервера.'
    if isinstance(exc, requests.RequestException):
        return f'Ошибка сети: {type(exc).__name__}.'
    return str(exc)[:300]


def related_publication_agent(trials: list[dict], current_publications: list[dict],
                              limit: int = 6) -> PublicationsResult | None:
    """Optional single batched follow-up: find RESULT/DERIVED cited papers in Europe PMC.

    Queries one Europe PMC endpoint rather than one network request per PMID.
    This enrichment is best-effort and cannot block the primary analysis.
    """
    already = {str(p.get('pmid')) for p in current_publications if p.get('pmid')}
    pmids = []
    for trial in trials:
        for ref in trial.get('references') or []:
            pmid = str(ref.get('pmid') or '')
            if (ref.get('type') in ('RESULT', 'DERIVED') and pmid.isdigit()
                    and pmid not in already and pmid not in pmids):
                pmids.append(pmid)
            if len(pmids) >= limit:
                break
        if len(pmids) >= limit:
            break
    if not pmids:
        return None
    query = '(' + ' OR '.join(f'EXT_ID:{pmid}' for pmid in pmids) + ') AND SRC:MED'
    return publication_agent(query, limit)


def coordinator(query: str, limit: int = 20,
                start_date: date | str | None = None,
                end_date: date | str | None = None,
                trial_date_field: str = 'StartDate',
                intervention: str = '',
                trial_status: str = '',
                trial_phase: str = '',
                results_filter: str = 'all',
                publication_type: str = 'all') -> dict:
    start, end = _normalize_period(start_date, end_date)
    if trial_date_field not in TRIAL_DATE_FIELDS:
        raise ValueError('Неизвестное поле даты для ClinicalTrials.gov.')
    if not query.strip():
        raise ValueError('Введите тему исследования')
    if trial_status and trial_status not in TRIAL_STATUSES:
        raise ValueError('Неизвестный статус клинического испытания.')
    if trial_phase and trial_phase not in TRIAL_PHASES:
        raise ValueError('Неизвестная фаза клинического испытания.')
    if results_filter not in RESULTS_FILTERS:
        raise ValueError('Неизвестный фильтр наличия результатов.')
    if publication_type not in PUBLICATION_TYPE_QUERIES:
        raise ValueError('Неизвестный тип научной публикации.')
    _intervention_phrase(intervention)
    limit = max(1, min(100, int(limit)))
    results: dict[str, list[dict]] = {'Europe PMC': [], 'ClinicalTrials.gov': []}
    errors: dict[str, str] = {}
    source_info: dict[str, dict] = {}

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {
            'Europe PMC': pool.submit(publication_agent, query.strip(), limit, start, end,
                                      intervention, publication_type),
            'ClinicalTrials.gov': pool.submit(trial_agent, query.strip(), limit, start, end,
                                              trial_date_field, intervention, trial_status,
                                              trial_phase, results_filter),
        }
        for name, future in futures.items():
            try:
                data = future.result()
                if name == 'Europe PMC':
                    results[name] = data.records
                    source_info[name] = {
                        'mode': data.mode, 'from_cache': data.from_cache,
                        'retrieved_at_utc': data.retrieved_at_utc, 'note': data.note,
                    }
                else:
                    results[name] = data
                    source_info[name] = {'mode': 'live', 'from_cache': False}
            except (requests.RequestException, ValueError, KeyError, TypeError, OSError) as exc:
                errors[name] = _concise_error(exc)
    linked = []
    # Trial references remain useful even if the primary publication query has
    # zero matches (e.g. a narrow publication-type filter).
    if results['ClinicalTrials.gov']:
        try:
            extra = related_publication_agent(results['ClinicalTrials.gov'], results['Europe PMC'])
            if extra is not None:
                linked = extra.records
                source_info['Дополнительные публикации'] = {
                    'mode': extra.mode, 'from_cache': extra.from_cache,
                    'retrieved_at_utc': extra.retrieved_at_utc, 'note': extra.note,
                }
        except (requests.RequestException, ValueError, KeyError, TypeError, OSError) as exc:
            # Optional enrichment: don't mark the already successful source as failed.
            source_info['Дополнительные публикации'] = {
                'note': 'Дополнительный поиск PMID не выполнен: ' + _concise_error(exc),
            }
    report = analysis_agent(query.strip(), results['Europe PMC'], results['ClinicalTrials.gov'],
                            errors, source_info, linked)
    report['search_filters'] = {
        'start_date': start,
        'end_date': end,
        'trial_date_field': trial_date_field,
        'intervention': intervention.strip(),
        'trial_status': trial_status,
        'trial_phase': trial_phase,
        'results_filter': results_filter,
        'publication_type': publication_type,
    }
    if intervention.strip():
        report['limitations'].append(
            'Препарат/метод ищется в тексте Europe PMC и отдельно в поле вмешательств ClinicalTrials.gov; '
            'результаты двух источников могут отличаться.'
        )
    if publication_type != 'all':
        report['limitations'].append(
            'Тип публикации определяется индексом Europe PMC, а не классификацией ClinicalTrials.gov.'
        )
    if any((trial_status, trial_phase, results_filter != 'all')):
        report['limitations'].append(
            'Статус, фаза и наличие результатов ограничивают только испытания ClinicalTrials.gov. '
            'Дополнительные публикации по PMID используются только для поиска связей.'
        )
    if start:
        report['limitations'].append(
            'Публикации отфильтрованы по дате первого выхода, испытания — по выбранному полю даты. '
            'Дополнительные статьи, найденные по PMID для связывания записей, могут выходить за пределы периода.'
        )
    return report
