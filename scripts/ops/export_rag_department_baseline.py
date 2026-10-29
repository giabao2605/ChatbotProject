"""Export catalog and sample hashes without changing SQL, runtime or secrets."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

from sqlalchemy import text

from mech_chatbot.config.settings import SqlSettings, load_settings
from mech_chatbot.db.engine import create_db_engine


DEPARTMENTS = ('Accountant', 'HR', 'HSE_5S', 'ISO', 'IT', 'Maintenance', 'Molding',
               'Planning', 'Production', 'Purchasing', 'QualityControl', 'Sales',
               'Technical', 'Warehouse')
SAMPLE_DIRS = ('9.1.00678', '9.3.03843', '9.3.03844', '9.3.03951', '9.3.04068', '9.3.04080')


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     default=str).encode('utf-8')).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--env-file', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    settings = load_settings(args.env_file)
    engine = create_db_engine(SqlSettings.from_settings(settings))
    try:
        with engine.connect() as conn:
            rows = [dict(row) for row in conn.execute(text(
                'SELECT DeptCode,DeptName,Domain,Site,IsActive,Status FROM dbo.Departments ORDER BY DeptCode'
            )).mappings()]
    finally:
        engine.dispose()
    inventory = []
    for folder in SAMPLE_DIRS:
        for path in sorted((args.source_root / folder).glob('*.pdf')):
            inventory.append({'path': str(path.relative_to(args.source_root)),
                              'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                              'bytes': path.stat().st_size})
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    configuration = {key: getattr(settings, key) for key in (
        'RAG_EXECUTION_CONTEXT', 'RAG_REQUEST_DEADLINE_SECONDS', 'EVALUATION_FORCE_AMBIGUOUS'
    ) if hasattr(settings, key)}
    configuration.update({key: value for key, value in settings.model_dump().items()
                          if key.startswith('FEATURE_')})
    payload = {'schema': 'department-readiness-v2', 'exported_at': datetime.now(timezone.utc).isoformat(),
               'source_commit': commit, 'config_hash': fingerprint(configuration),
               'configuration': configuration, 'catalog_source': 'live_read_only_select',
               'catalog': [{'code': row['DeptCode'], 'active': bool(row['IsActive']) and row['Status'] == 'active'}
                           for row in rows if row['DeptCode'] in DEPARTMENTS],
               'catalog_rows': rows, 'sample_inventory': inventory,
               'snapshot_fingerprint': fingerprint({'catalog': rows, 'samples': inventory}),
               'departments': [{'code': code, 'owner': None, 'approver': None, 'questions': []}
                               for code in DEPARTMENTS], 'runs': [],
               'accepted': False, 'offline_only': True}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / 'baseline.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    report = ('# Baseline Advanced RAG cho 14 phòng ban\n\n'
              f'Commit khởi điểm: `{commit}`. Danh mục được đọc trực tiếp bằng SELECT; không sửa dữ liệu.\n\n'
              f'Phạm vi người dùng đã xác nhận: 14 phòng, tối thiểu 1.050 câu hỏi thật. Bộ mẫu bổ sung: {len(inventory)} PDF trong sáu thư mục.\n\n'
              'Chưa có bộ câu hỏi thật và xác nhận người phụ trách/người duyệt trong artifact này. '
              'Kết quả unit/fixture không cấp quyền bật live. Migration, nghiệm thu nghiệp vụ và cửa sổ live mới vẫn cần bằng chứng riêng.\n\n'
              '| Phòng | Câu thật trong manifest | Trạng thái |\n|---|---:|---|\n' +
              ''.join(f'| {code} | 0 | Chưa đủ bằng chứng |\n' for code in DEPARTMENTS))
    (args.output_dir / 'baseline.md').write_text(report, encoding='utf-8')
    print(json.dumps({'departments': len(payload['catalog']), 'sample_pdfs': len(inventory), 'accepted': False}))


if __name__ == '__main__':
    main()
