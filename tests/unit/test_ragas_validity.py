import importlib.util
from pathlib import Path


def _metrics():
    path = Path(__file__).resolve().parents[2] / 'scripts/eval/ragas_metrics.py'
    spec = importlib.util.spec_from_file_location('ragas_validity_metrics', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _report(metrics, value):
    return metrics.aggregate([{'id': 'one', 'domain': 'generic',
                              'metrics': {name: value for name in metrics.METRIC_NAMES}}])


def test_missing_scores_cannot_pass_numeric_baseline():
    metrics = _metrics()
    result = metrics.compare_baseline(_report(metrics, None), _report(metrics, .95))
    assert result['passed'] is False
    assert result['valid'] is False


def test_missing_baseline_is_not_a_pass():
    metrics = _metrics()
    assert metrics.compare_baseline(_report(metrics, .95), None)['passed'] is False


def test_partial_and_nonfinite_scores_are_invalid():
    metrics = _metrics()
    for bad in (None, float('nan'), float('inf'), True, 1.1):
        case = {'id': 'one', 'metrics': {name: .9 for name in metrics.METRIC_NAMES}}
        case['metrics']['faithfulness'] = bad
        assert metrics.aggregate([case])['valid'] is False


def test_valid_baseline_requires_same_case_ids():
    metrics = _metrics()
    baseline = _report(metrics, .9)
    current = _report(metrics, .95)
    current['case_ids'] = ['different']
    assert metrics.compare_baseline(current, baseline)['passed'] is False


def test_declared_not_applicable_metric_is_not_missing():
    metrics = _metrics()
    case = {'id': 'one', 'required_metrics': ['faithfulness'],
            'metrics': {'faithfulness': .9}}
    assert metrics.aggregate([case])['valid'] is True


def test_valid_numeric_scores_compare_and_render_invalid_honestly():
    metrics = _metrics()
    baseline = _report(metrics, .9)
    assert metrics.compare_baseline(_report(metrics, .92), baseline)['passed'] is True
    invalid = metrics.compare_baseline(_report(metrics, None), baseline)
    text = metrics.render_markdown(_report(metrics, None), baseline, invalid)
    assert 'INVALID' in text
    assert 'KHONG co regression' not in text
