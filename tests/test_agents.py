from unittest.mock import patch
from agents import publication_agent, trial_agent, analysis_agent, coordinator


def test_publication_parsing():
    data = {'resultList': {'result': [{'id': '12345', 'source': 'MED', 'title': 'Test paper', 'abstractText': '<p>Study NCT12345678.</p>', 'pubYear': '2024'}]}}
    with patch('agents._get_json', return_value=data):
        results = publication_agent('test', 10)
    assert len(results) == 1
    assert results[0]['nct_ids'] == ['NCT12345678']
    assert results[0]['abstract'] == 'Study NCT12345678.'


def test_trial_parsing():
    data = {'studies': [{'protocolSection': {'identificationModule': {'nctId': 'NCT12345678', 'briefTitle': 'Test trial'}, 'statusModule': {'overallStatus': 'RECRUITING'}, 'designModule': {'phases': ['PHASE2']}}}]}
    with patch('agents._get_json', return_value=data):
        results = trial_agent('test', 10)
    assert results[0]['phase'] == 'PHASE2'
    assert results[0]['url'].endswith('NCT12345678')


def test_match_aggregation():
    pubs = [{'title': 'Test paper', 'url': 'https://example.org/p', 'year': '2024', 'nct_ids': ['NCT12345678']}]
    trials = [{'nct_id': 'NCT12345678', 'title': 'Test trial', 'url': 'https://example.org/t', 'status': 'RECRUITING', 'phase': 'PHASE2', 'has_results': False}]
    report = analysis_agent('test', pubs, trials, {})
    assert report['counts']['publications'] == 1
    assert len(report['matches']) == 1


def test_coordinator_partial_failure():
    with patch('agents.publication_agent', side_effect=ValueError('problem')):
        with patch('agents.trial_agent', return_value=[]):
            report = coordinator('test', 10)
    assert 'Europe PMC' in report['errors']
    assert report['counts']['trials'] == 0
