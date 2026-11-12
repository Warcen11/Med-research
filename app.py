"""Medical Research Explorer: two collection agents + explainable linking/analysis."""
import json
from datetime import date

import pandas as pd
import streamlit as st

from agents import coordinator

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
            st.session_state['report'] = coordinator(
                query, limit, start_date, end_date, trial_date_field,
            )

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

pub_tab, trial_tab, analytics_tab, report_tab = st.tabs(
    ['📄 Публикации', '🧪 Испытания', '📊 Аналитика', '📥 Отчёт'])

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
        st.dataframe(df[['nct_id', 'title', 'status', 'phase', 'start_date',
                         'first_posted_date', 'has_results', 'url']],
                     use_container_width=True, hide_index=True,
                     column_config={'url': st.column_config.LinkColumn('Открыть'),
                                    'has_results': 'Результаты в реестре'})
        selected = st.selectbox('Описание испытания', range(len(trials)),
                                format_func=lambda i: f'{trials[i]["nct_id"]}: {trials[i]["title"]}')
        trial = trials[selected]
        st.write(trial.get('summary') or 'Описание отсутствует.')
        st.write('Заболевания:', trial.get('conditions') or 'не указаны')
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

with report_tab:
    st.markdown('### Сводный отчёт агента-аналитика')
    st.write(f'**Тема:** {report["query"]}')
    if filters.get('start_date'):
        st.write(f'**Период поиска:** {filters["start_date"]} — {filters["end_date"]}')
    else:
        st.write('**Период поиска:** без ограничений')
    st.write('**Краткая сводка по найденным записям:**')
    for line in report.get('highlights', []):
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
