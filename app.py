"""Medical Research Explorer: two collection agents + explainable linking/analysis."""
import json
from datetime import date

import pandas as pd
import streamlit as st

from agents import coordinator, research_analysis, STATUS_LABELS

PUBLICATION_TYPES = {
    'all': 'Все публикации',
    'review': 'Обзоры (review)',
    'clinical_trial': 'Клинические испытания (статьи)',
    'randomized_trial': 'Рандомизированные испытания (RCT)',
    'meta_analysis': 'Метаанализы',
}
PHASE_NAMES = {
    '': 'Любая фаза',
    'EARLY_PHASE1': 'Ранняя I',
    'PHASE1': 'I',
    'PHASE2': 'II',
    'PHASE3': 'III',
    'PHASE4': 'IV',
    'NA': 'Не применяется',
}
RESULT_OPTIONS = {
    'all': 'Любое наличие',
    'with': 'Только с результатами в реестре',
    'without': 'Только без результатов в реестре',
}

st.set_page_config(page_title='Medical Research Explorer', page_icon='🔬', layout='wide')
st.title('🔬 Medical Research Explorer')
st.caption('Мультиагентный поиск публикаций, клинических испытаний и связей между ними')

with st.sidebar:
    st.header('Параметры поиска')
    query = st.text_input('Тема (на английском)', value='Alzheimer disease',
                          help='Например: type 2 diabetes, Alzheimer disease, breast cancer')
    limit = st.slider('Записей из каждого источника', 5, 50, 20, 5)
    use_period = st.checkbox('Ограничить поиск по датам', value=False)
    start_date = end_date = None
    trial_date_field = 'StartDate'
    if use_period:
        start_date = st.date_input('С', value=date(date.today().year - 5, 1, 1))
        end_date = st.date_input('По', value=date.today())
        trial_date_mode = st.selectbox(
            'Дата для клинических исследований',
            ['Начало исследования', 'Первая публикация в реестре'],
        )
        if trial_date_mode == 'Первая публикация в реестре':
            trial_date_field = 'StudyFirstPostDate'
        st.caption('Europe PMC: дата первой публикации статьи. ClinicalTrials.gov: выбранная дата исследования.')
    with st.expander('🔎 Расширенный поиск', expanded=True):
        intervention = st.text_input(
            'Препарат или метод лечения (необязательно)',
            placeholder='Например: donanemab, cognitive therapy',
            help='В Europe PMC уточняет текстовый поиск, в ClinicalTrials.gov — поле вмешательства.',
        )
        publication_type = st.selectbox(
            'Тип научной публикации · Europe PMC',
            options=list(PUBLICATION_TYPES),
            format_func=lambda key: PUBLICATION_TYPES[key],
        )
        trial_status = st.selectbox(
            'Статус испытания · ClinicalTrials.gov',
            options=[''] + [
                'COMPLETED', 'RECRUITING', 'NOT_YET_RECRUITING',
                'ACTIVE_NOT_RECRUITING', 'TERMINATED', 'SUSPENDED', 'WITHDRAWN',
            ],
            format_func=lambda key: STATUS_LABELS.get(key, key) if key else 'Любой статус',
        )
        trial_phase = st.selectbox(
            'Фаза испытания · ClinicalTrials.gov',
            options=list(PHASE_NAMES),
            format_func=lambda key: PHASE_NAMES[key],
        )
        results_filter = st.selectbox(
            'Опубликованные результаты · ClinicalTrials.gov',
            options=list(RESULT_OPTIONS),
            format_func=lambda key: RESULT_OPTIONS[key],
        )
        st.caption('Фильтры действуют на API до ограничения числа записей. '
                   'Статус, фаза и результаты относятся только к испытаниям.')
    search = st.button('🔎 Запустить агентов', type='primary', use_container_width=True)
    st.divider()
    st.markdown('**Источники**')
    st.markdown('• [Europe PMC](https://europepmc.org/)  \n• [ClinicalTrials.gov](https://clinicaltrials.gov/)')
    st.caption('Координатор запускает двух сборщиков параллельно, затем сопоставляет записи по NCT ID и PMID.')

if search:
    if not query.strip():
        st.error('Введите тему исследования.')
    elif use_period and start_date > end_date:
        st.error('Дата «С» не может быть позже даты «По».')
    else:
        with st.spinner('Агенты собирают данные, проверяют ссылки и готовят отчёт…'):
            try:
                st.session_state['report'] = coordinator(
                    query, limit, start_date, end_date, trial_date_field,
                    intervention=intervention, trial_status=trial_status,
                    trial_phase=trial_phase, results_filter=results_filter,
                    publication_type=publication_type,
                )
            except ValueError as exc:
                st.error(str(exc))

if 'report' not in st.session_state:
    st.markdown('### Как начать')
    st.write('Введите тему в боковой панели и нажмите **«Запустить агентов»**.')
    st.stop()

report = st.session_state['report']
counts = report['counts']
st.subheader(f'Результаты: {report["query"]}')
st.caption(f'Дата обработки (UTC): {report["collected_at_utc"]}')
filters = report.get('search_filters') or {}
if filters.get('start_date'):
    trial_date_label = ('дате первой регистрации' if filters.get('trial_date_field') == 'StudyFirstPostDate'
                        else 'дате начала')
    st.caption(f'Период: {filters["start_date"]} — {filters["end_date"]} · '
               f'статьи по дате публикации, испытания по {trial_date_label}.')
else:
    st.caption('Поиск без ограничений по датам.')

active_filters = []
if filters.get('intervention'):
    active_filters.append('Препарат/метод: ' + filters['intervention'])
if filters.get('publication_type', 'all') != 'all':
    active_filters.append('Публикации: ' + PUBLICATION_TYPES.get(filters['publication_type'], filters['publication_type']))
if filters.get('trial_status'):
    active_filters.append('Статус: ' + STATUS_LABELS.get(filters['trial_status'], filters['trial_status']))
if filters.get('trial_phase'):
    active_filters.append('Фаза: ' + PHASE_NAMES.get(filters['trial_phase'], filters['trial_phase']))
if filters.get('results_filter', 'all') != 'all':
    active_filters.append('Результаты: ' + RESULT_OPTIONS.get(filters['results_filter'], filters['results_filter']))
if active_filters:
    st.caption('Активные фильтры: ' + ' · '.join(active_filters))

for source, error in report['errors'].items():
    st.error(f'{source}: {error} Результаты остальных источников сохранены.')
for source, info in report.get('source_info', {}).items():
    if info.get('note'):
        st.warning(f'{source}: {info["note"]}')

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric('Публикации', counts['publications'])
c2.metric('Испытания', counts['trials'])
c3.metric('Завершено', counts['completed_trials'])
c4.metric('С результатами в реестре', counts['trials_with_results'])
c5.metric('Связи по ID', len(report['matches']))
st.caption('Счётчики — только по загруженным записям. Дополнительно найденные публикации в основной счётчик не входят.')

pub_tab, trial_tab, analytics_tab, research_tab, report_tab = st.tabs(
    ['📄 Публикации', '🧪 Испытания', '📊 Аналитика',
     '🔬 Исследовательский анализ', '📥 Отчёт'])

with pub_tab:
    pubs = report['publications']
    if pubs:
        df = pd.DataFrame(pubs)
        st.dataframe(df[['title', 'year', 'authors', 'journal', 'pmid', 'url']],
                     use_container_width=True, hide_index=True,
                     column_config={'url': st.column_config.LinkColumn('Ссылка'),
                                    'pmid': 'PMID'})
        selected = st.selectbox('Аннотация публикации', range(len(pubs)),
                                format_func=lambda i: pubs[i].get('title') or f'Публикация {i+1}')
        st.write(pubs[selected].get('abstract') or 'Аннотация отсутствует в полученных метаданных.')
        if pubs[selected].get('url'):
            st.markdown(f'[Открыть публикацию]({pubs[selected]["url"]})')
    else:
        st.info('Публикации не найдены или источник недоступен.')

with trial_tab:
    trials = report['trials']
    if trials:
        df = pd.DataFrame(trials)
        if 'interventions' not in df:  # support viewing an older report in session state
            df['interventions'] = ''
        st.dataframe(df[['nct_id', 'title', 'status', 'phase', 'interventions', 'start_date',
                         'first_posted_date', 'has_results', 'url']],
                     use_container_width=True, hide_index=True,
                     column_config={'url': st.column_config.LinkColumn('Открыть'),
                                    'has_results': 'Результаты в реестре'})
        selected = st.selectbox('Описание испытания', range(len(trials)),
                                format_func=lambda i: f'{trials[i]["nct_id"]}: {trials[i]["title"]}')
        trial = trials[selected]
        st.write(trial.get('summary') or 'Описание отсутствует.')
        st.write('Заболевания:', trial.get('conditions') or 'не указаны')
        st.write('Вмешательства:', trial.get('interventions') or 'не указаны')
        references = trial.get('references') or []
        if references:
            with st.expander(f'Публикации, указанные в реестре ({len(references)})'):
                st.dataframe(pd.DataFrame(references), hide_index=True, use_container_width=True)
                st.caption('BACKGROUND — фоновая статья; RESULT/DERIVED — ссылки, относящиеся к результатам или данным испытания.')
    else:
        st.info('Испытания не найдены или источник недоступен.')

with analytics_tab:
    st.markdown('### Состояние исследований в выбранной выдаче')
    left, right = st.columns(2)
    with left:
        st.markdown('#### Статусы клинических испытаний')
        statuses = report['status_counts']
        if statuses:
            df = pd.DataFrame([{'Статус': k, 'Количество': v} for k, v in statuses.items()])
            st.bar_chart(df, x='Статус', y='Количество', horizontal=True,
                         height=max(280, len(df)*45), use_container_width=True)
        else:
            st.info('Нет данных об испытаниях.')
    with right:
        st.markdown('#### Фазы клинических испытаний')
        phases = report['phase_counts']
        if phases:
            df = pd.DataFrame([{'Фаза': k, 'Количество': v} for k, v in phases.items()])
            st.bar_chart(df, x='Фаза', y='Количество', horizontal=True,
                         height=max(280, len(df)*45), use_container_width=True)
            st.caption('Многофазные испытания показаны отдельными комбинациями. «Не применяется» ≠ «Не указана».')
        else:
            st.info('Нет данных о фазах.')

    st.markdown('#### Публикации по годам в текущей выборке')
    years = report['year_counts']
    if years:
        year_df = pd.DataFrame([{'Год': year, 'Количество': value} for year, value in years.items()])
        st.bar_chart(year_df, x='Год', y='Количество', use_container_width=True)
        if len(years) == 1:
            st.info('Все загруженные публикации относятся к одному году. Это не динамика научной активности: '
                    'поисковая выдача ограничена и отсортирована по релевантности.')
        else:
            st.caption('Это распределение загруженных публикаций, а не всех публикаций по теме в базах.')
    else:
        st.info('Нет данных о годах публикаций.')

    st.markdown('### Проверяемые связи публикаций и испытаний')
    st.caption('Сопоставление: NCT ID в статье или PMID публикации в списке ссылок испытания (RESULT/DERIVED).')
    matches = report['matches']
    if matches:
        df = pd.DataFrame(matches)
        df['Основание связи'] = df['methods'].apply(', '.join)
        st.dataframe(df[['nct_id', 'trial_title', 'publication_title', 'Основание связи',
                         'publication_url', 'trial_url']],
                     use_container_width=True, hide_index=True,
                     column_config={'publication_url': st.column_config.LinkColumn('Публикация'),
                                    'trial_url': st.column_config.LinkColumn('Испытание')})
        st.caption('Упоминание NCT ID — это свидетельство связи, но не доказательство публикации результатов испытания.')
    else:
        st.info('Связей по NCT ID / PMID в загруженной выборке нет. Это не означает отсутствия связей в источниках.')

    if report.get('related_publications'):
        with st.expander(f'Дополнительно найдены публикации по PMID ({len(report["related_publications"])})'):
            if filters.get('start_date'):
                st.caption('Для проверки связи с испытаниями эти статьи найдены по PMID без фильтра дат.')
            st.dataframe(pd.DataFrame(report['related_publications'])[['title', 'year', 'pmid', 'url']],
                         hide_index=True, use_container_width=True,
                         column_config={'url': st.column_config.LinkColumn('Публикация')})
    if report.get('background_matches'):
        with st.expander(f'Фоновая литература (не результаты испытаний): {len(report["background_matches"])}'):
            st.dataframe(pd.DataFrame(report['background_matches'])[
                ['nct_id', 'publication_title', 'publication_url']],
                hide_index=True, use_container_width=True,
                column_config={'publication_url': st.column_config.LinkColumn('Открыть')})

with research_tab:
    st.markdown('### Исследовательский анализ клинических испытаний')
    st.caption('Используются поля найденных записей ClinicalTrials.gov. '
               'Дополнительных запросов и экспертной оценки эффективности не выполняется.')
    full_analysis = report.get('research_analysis') or research_analysis(report['trials'])

    if not report['trials']:
        st.info('Для исследовательского анализа нужны записи об испытаниях. '
                'Проверьте запрос и выбранные фильтры.')
    else:
        st.markdown('#### 1. Какие методы и препараты изучают')
        methods = full_analysis['interventions']
        if methods:
            method_df = pd.DataFrame([
                {'Вмешательство': item['name'], 'Тип': item['type'],
                 'Испытаний': item['count']}
                for item in methods[:20]
            ])
            st.dataframe(method_df, hide_index=True, use_container_width=True)
            st.caption('Одно испытание может изучать несколько вмешательств. '
                       'Явно обозначенные placebo/sham и стандартный уход исключены из этого рейтинга, '
                       'но сохранены в исходных записях. Синонимы препаратов не объединяются автоматически.')
            options = [''] + list(range(len(methods)))
            selected = st.selectbox(
                'Анализ конкретного препарата или метода', options,
                format_func=lambda x: ('Все найденные испытания' if x == '' else
                                       f'{methods[x]["name"]} — {methods[x]["count"]} испытаний'),
                key=f'research_method_choice_{report["collected_at_utc"]}',
            )
            if selected == '':
                selected_trials = report['trials']
            else:
                ids = set(methods[selected]['nct_ids'])
                selected_trials = [trial for trial in report['trials']
                                   if trial.get('nct_id') in ids]
            chosen_ids = {t.get('nct_id') for t in selected_trials}
            analysis = research_analysis(selected_trials, [
                match for match in report['matches'] if match.get('nct_id') in chosen_ids
            ])
        else:
            st.info('В загруженных испытаниях нет названий вмешательств, '
                    'подходящих для группировки. Остальная аналитика доступна ниже.')
            analysis = full_analysis
            selected_trials = report['trials']

        st.divider()
        st.markdown('#### 2. Методология и масштаб')
        st.caption('Показатели дизайна относятся к интервенционным испытаниям. '
                   'Незаполненное поле не трактуется как отсутствие соответствующего метода.')
        design = analysis
        num_interventional = design['n_interventional']
        randomized = design['allocation_counts'].get('Рандомизировано', 0)
        masked = sum(n for k, n in design['masking_counts'].items()
                     if k not in ('Без ослепления', 'Не указано'))
        m1, m2, m3, m4 = st.columns(4)
        m1.metric('В выборке', design['total'])
        m2.metric('Интервенционные', num_interventional)
        m3.metric('Рандомизированные', f'{randomized}/{num_interventional}')
        m4.metric('С ослеплением', f'{masked}/{num_interventional}')

        p1, p2 = st.columns(2)
        with p1:
            st.markdown('**Рандомизация**')
            if design['allocation_counts']:
                st.dataframe(pd.DataFrame([
                    {'Категория': k, 'Испытаний': v}
                    for k, v in design['allocation_counts'].items()
                ]), hide_index=True, use_container_width=True)
            else:
                st.info('Нет интервенционных испытаний.')
        with p2:
            st.markdown('**Ослепление**')
            if design['masking_counts']:
                st.dataframe(pd.DataFrame([
                    {'Категория': k, 'Испытаний': v}
                    for k, v in design['masking_counts'].items()
                ]), hide_index=True, use_container_width=True)
            else:
                st.info('Нет интервенционных испытаний.')

        reported = design['enrollment_reported']
        actual_reported = design['enrollment_actual_reported']
        st.write('**Количество участников:** медиана по записям с указанной численностью — '
                 f'**{design["enrollment_median"]:g}** '
                 if design['enrollment_median'] is not None else
                 '**Количество участников:** данные в выборке не указаны.')
        if reported:
            st.caption(f'Численность указана в {reported} из {design["total"]} записей. '
                       f'У {actual_reported} она помечена как фактическая, '
                       'у остальных может быть запланированной. '
                       'Медиана по всей выборке смешивает эти типы и приведена для справки.')
            if design['enrollment_actual_median'] is not None:
                st.caption('Медиана только фактической численности: '
                           f'{design["enrollment_actual_median"]:g} участников.')

        st.divider()
        st.markdown('#### 3. Доступность результатов исследований')
        e1, e2, e3 = st.columns(3)
        e1.metric('Результаты в реестре', design['results_in_registry'])
        e2.metric('Ссылки RESULT в реестре', design['result_reference_trials'])
        e3.metric('Связано с Europe PMC по PMID', design['confirmed_result_trial_count'])
        evidence = design['evidence_counts']
        evidence_df = pd.DataFrame([
            {'Категория': label, 'Испытаний': amount}
            for label, amount in evidence.items()
        ])
        st.dataframe(evidence_df, hide_index=True, use_container_width=True)
        st.caption('RESULT — тип ссылки, указанный в записи ClinicalTrials.gov. '
                   'Совпадение с Europe PMC по PMID подтверждает наличие связанной статьи, '
                   'но не её медицинские выводы. Отсутствие результатов и ссылок в реестре '
                   'не доказывает отсутствия публикаций вообще.')
        if design['confirmed_result_links']:
            with st.expander('Связанные публикации результатов в Europe PMC'):
                st.dataframe(pd.DataFrame(design['confirmed_result_links']),
                             hide_index=True, use_container_width=True,
                             column_config={
                                 'publication_url': st.column_config.LinkColumn('Публикация'),
                                 'trial_url': st.column_config.LinkColumn('Испытание'),
                             })

        with st.expander('Проверить данные по каждому испытанию'):
            detail_rows = design['trial_details']
            if detail_rows:
                st.dataframe(pd.DataFrame(detail_rows)[[
                    'nct_id', 'title', 'study_type', 'allocation', 'masking',
                    'enrollment', 'enrollment_type', 'registry_results',
                    'result_reference', 'url'
                ]], hide_index=True, use_container_width=True,
                    column_config={
                        'registry_results': 'Результаты в реестре',
                        'result_reference': 'Ссылка RESULT',
                        'url': st.column_config.LinkColumn('Открыть исследование'),
                    })
            else:
                st.info('Для выбранного вмешательства записей нет.')

        st.divider()
        st.markdown('#### 4. Автоматические наблюдения')
        for observation in design['observations']:
            st.markdown('- ' + observation)
        st.caption('Это алгоритмическая сводка метаданных, а не медицинский вывод. '
                   'Доли и частоты относятся исключительно к загруженной выборке.')

with report_tab:
    st.markdown('### Сводный отчёт агента-аналитика')
    st.write(f'**Тема:** {report["query"]}')
    if filters.get('start_date'):
        st.write(f'**Период поиска:** {filters["start_date"]} — {filters["end_date"]}')
    else:
        st.write('**Период поиска:** без ограничений')
    if active_filters:
        st.write('**Дополнительные фильтры:** ' + '; '.join(active_filters))
    st.write('**Краткая сводка по найденным записям:**')
    for line in report.get('highlights', []):
        st.write('•', line)
    st.markdown('**Исследовательские наблюдения:**')
    for line in (report.get('research_analysis') or research_analysis(report['trials']))['observations']:
        st.write('•', line)
    if report.get('related_publications'):
        st.write(f'Дополнительно получено **{len(report["related_publications"])}** публикаций '
                 'по PMID из реестра клинических испытаний. Они учитываются отдельно от основной выдачи.')
    if report['errors']:
        st.warning('Отчёт неполон: часть источников вернула ошибку.')
    st.markdown('**Ограничения интерпретации:**')
    for line in report['limitations']:
        st.write('•', line)
    st.download_button('📥 Скачать полный отчёт (JSON)',
                       data=json.dumps(report, ensure_ascii=False, indent=2),
                       file_name='medical_research_report.json', mime='application/json')
    for label, records, filename in [
        ('публикации', report['publications'], 'publications.csv'),
        ('испытания', report['trials'], 'trials.csv'),
        ('связи', report['matches'], 'matches.csv'),
    ]:
        if records:
            data = pd.DataFrame(records).to_csv(index=False).encode('utf-8-sig')
            st.download_button(f'📥 Скачать {label} (CSV)', data=data,
                               file_name=filename, mime='text/csv')
