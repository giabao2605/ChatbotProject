import { expect, test } from '@playwright/test';

test('offline detail scope excludes subtotal with a reason and restores the saved selection', async ({ page }) => {
  const writes: Record<string, unknown>[] = [];
  let saved: Record<string, unknown> = {};
  await page.route(url => url.pathname.startsWith('/api/'), async route => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    let body: unknown = {};
    if (req.method() !== 'GET') {
      if (path !== '/api/rag-evidence/scopes/detail/review') return route.abort();
      saved = req.postDataJSON();
      writes.push(saved);
      body = { ok: true };
    } else if (path === '/api/auth/me') {
      body = { ok: true, user: { user_id: 7, username: 'offline-reviewer', roles: ['reviewer'], department: 'Accountant',
        allowed_departments: ['Accountant'], allowed_sites: ['HQ'], max_security_level: 'internal', csrf_token: 'offline-csrf', preferred_language: 'vi' } };
    } else if (path === '/api/documents/pending-review') {
      body = { documents: [{ DocID: 17, JobID: 1, TenFile: 'detail-subtotal.csv', ThuMuc: 'Accountant' }] };
    } else if (path === '/api/rag-evidence/documents/17/scopes') {
      body = { scopes: [{ scope_id: 'detail', snapshot_id: 'offline-export', completeness_reviewed: writes.length > 0, ...saved }] };
    } else if (path === '/api/rag-evidence/scopes/detail/facts') {
      body = { items: [['detail-1', '100'], ['detail-2', '200'], ['subtotal', '300']].map(([row, value]) => ({
        fact_id: row, label: row === 'subtotal' ? 'Dòng tổng' : 'Chi tiết', raw_value: value, value, unit: 'VND', currency: 'VND',
        review_status: 'approved', conflicts: [], locator: { doc_id: '17', version: '1', sheet: 'CSV', row_key: row },
      })), offset: 0, has_more: false };
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  });
  await page.goto('/review');
  await page.getByRole('button', { name: 'Duyệt dữ liệu có cấu trúc', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Duyệt dữ liệu có cấu trúc' });
  await expect(dialog.locator('tbody tr')).toHaveCount(3);
  await dialog.getByLabel('Mã dòng được tính, mỗi mã một dòng').fill('detail-1\ndetail-2');
  await dialog.getByLabel('Định nghĩa số liệu', { exact: true }).fill('Tổng chi tiết đã đối soát');
  await dialog.getByRole('button', { name: 'Thêm dòng loại trừ', exact: true }).click();
  await dialog.getByLabel('Mã dòng loại trừ 1', { exact: true }).fill('subtotal');
  await dialog.getByLabel('Lý do loại trừ 1', { exact: true }).fill('Dòng tổng đã bao gồm hai dòng chi tiết');
  await dialog.locator('input[name=scope_checked]').check();
  await dialog.getByRole('button', { name: 'Xác nhận phạm vi đầy đủ', exact: true }).click();
  await expect.poll(() => writes.length).toBe(1);
  expect(writes[0]).toMatchObject({ expected_row_ids: ['detail-1', 'detail-2'],
    excluded_row_reasons: { subtotal: 'Dòng tổng đã bao gồm hai dòng chi tiết' } });
  expect(writes[0]).not.toHaveProperty('reviewer_id');
  await expect(dialog.getByRole('status')).toHaveText('Đã lưu xác nhận phạm vi đầy đủ.');
  await dialog.getByRole('button', { name: 'Tải lại', exact: true }).click();
  await expect(dialog.getByLabel('Mã dòng được tính, mỗi mã một dòng')).toHaveValue('detail-1\ndetail-2');
  await expect(dialog.getByLabel('Mã dòng loại trừ 1', { exact: true })).toHaveValue('subtotal');
  await expect(dialog.getByLabel('Lý do loại trừ 1', { exact: true })).toHaveValue('Dòng tổng đã bao gồm hai dòng chi tiết');
  await expect(dialog.locator('tbody tr')).toHaveCount(3);
  await expect(dialog.locator('input[name=scope_checked]')).not.toBeChecked();
  expect(writes).toHaveLength(1);
});

test('offline empty export review requires source inspection and explicit zero-row attestation', async ({ page }) => {
  const writes: Record<string, unknown>[] = [];
  await page.route(url => url.pathname.startsWith('/api/'), async route => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    let body: unknown = {};
    if (req.method()!=='GET') {
      if (path!=='/api/rag-evidence/scopes/empty/review') return route.abort();
      writes.push(req.postDataJSON());
      body = { ok:true };
    } else if (path==='/api/auth/me') {
      body = { ok:true, user:{user_id:7, username:'offline-reviewer', roles:['reviewer'], department:'Technical',
        allowed_departments:['Technical'], allowed_sites:['HQ'], max_security_level:'internal', csrf_token:'offline-csrf', preferred_language:'vi'} };
    } else if (path==='/api/documents/pending-review') {
      body = { documents:[{DocID:17, JobID:1, TenFile:'empty-fixture.csv', ThuMuc:'Technical'}] };
    } else if (path.endsWith('/scopes')) {
      body = { scopes:[{scope_id:'empty', snapshot_id:'export', completeness_reviewed:false, unit:'VND', currency:'VND',
        empty_source_locator:{doc_id:'17', version:'3', sheet:'CSV', cell:'B1', row_key:'', table_index:1},
        empty_source_hash:'a'.repeat(64), empty_source_extraction_method:'csv_header_only'}] };
    } else if (path.endsWith('/facts')) {
      body = {items:[], offset:0, has_more:false};
    }
    await route.fulfill({status:200, contentType:'application/json', body:JSON.stringify(body)});
  });
  await page.goto('/review');
  await page.getByRole('button', {name:'Duyệt dữ liệu có cấu trúc', exact:true}).click();
  const dialog = page.getByRole('dialog', {name:'Duyệt dữ liệu có cấu trúc'});
  await expect(dialog.getByText('Nguồn khai báo không có dòng số liệu.', {exact:false})).toBeVisible();
  await expect(dialog.locator('a[data-source=empty-original]')).toHaveAttribute('href','/api/files/documents/17/original');
  const submit = dialog.getByRole('button', {name:'Xác nhận phạm vi đầy đủ', exact:true});
  await expect(submit).toBeDisabled();
  await dialog.locator('input[name=definition]').fill('Chi phí kỳ đã đối soát');
  await dialog.locator('textarea[name=scope_note]').fill('Đã mở nguồn và xác nhận kỳ không có dòng');
  await dialog.locator('input[name=scope_checked]').check();
  await expect(submit).toBeEnabled();
  await submit.click();
  await expect.poll(() => writes.length).toBe(1);
  expect(writes[0]).toMatchObject({expected_row_ids:[], unit:'VND', currency:'VND', definition:'Chi phí kỳ đã đối soát'});
  expect(writes[0]).not.toHaveProperty('approved');
  expect(writes[0]).not.toHaveProperty('reviewer_id');
});

test('offline evidence review preserves raw values and pagination', async ({ page }, testInfo) => {
  const writes: Array<{ path: string; body: Record<string, unknown> }> = [];
  let sequenceApproved = false;
  const source = { fact_id: 'fact-1', label: 'Số lượng', raw_value: 'thiếu số lượng',
    value: null, unit: 'pcs', currency: '', review_status: 'pending', conflicts: ['missing_quantity'],
    locator: { doc_id: '17', version: '3', page: 2, row_key: 'row-1' } };
  const material = { ...source, fact_id: 'material-1', kind: 'material', label: 'Part A',
    raw_value: 'Inox 304', value: 'Inox 304', bom_position: '7',
    candidates: ['Description: Inox 201'], conflicts: ['material_conflict'] };
  // Every API request is intercepted. This fixture never reaches a live service.
  await page.route(url => url.pathname.startsWith('/api/'), async route => {
    const req = route.request();
    const url = new URL(req.url());
    let body: unknown = {};
    if (req.method() !== 'GET') {
      if (!['/api/rag-evidence/facts/fact-1/review', '/api/rag-evidence/text-facts/material-1/review', '/api/rag-evidence/documents/17/assembly-sequence/review'].includes(url.pathname)) return route.abort();
      writes.push({ path: url.pathname, body: req.postDataJSON() });
      if (url.pathname.endsWith('/assembly-sequence/review')) sequenceApproved = true;
      body = { ok: true };
    } else if (url.pathname === '/api/auth/me') {
      body = { ok: true, user: { user_id: 7, username: 'offline-reviewer', roles: ['reviewer'],
        department: 'Technical', allowed_departments: ['Technical'], allowed_sites: ['HQ'],
        max_security_level: 'internal', csrf_token: 'offline-csrf', preferred_language: 'vi' } };
    } else if (url.pathname === '/api/documents/pending-review') {
      body = { documents: [{ DocID: 17, JobID: 1, TenFile: 'drawing-fixture.pdf', ThuMuc: 'Technical' }] };
    } else if (url.pathname.endsWith('/scopes')) {
      body = { scopes: [{ scope_id: 'scope-1', snapshot_id: 'snapshot-1', completeness_reviewed: false }] };
    } else if (url.pathname.endsWith('/assembly-sequence')) {
      body = { status: sequenceApproved ? 'complete' : 'unknown', snapshot_id: 'snapshot-17',
        expected_step_numbers: sequenceApproved ? [1,2,3,4,5,6] : [],
        definition: sequenceApproved ? 'Toàn bộ sáu bước trong bản vẽ' : '' };
    } else if (url.pathname.endsWith('/text-facts')) {
      body = { items: [material], offset: 0, limit: 50, has_more: false };
    } else if (url.pathname.endsWith('/facts')) {
      body = { items: [source], offset: Number(url.searchParams.get('offset') || 0), limit: 50,
        has_more: !url.searchParams.get('offset') || url.searchParams.get('offset') === '0' };
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  });
  await page.goto('/review');
  await page.getByRole('button', { name: 'Duyệt dữ liệu có cấu trúc', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Duyệt dữ liệu có cấu trúc' });
  await expect(dialog.getByText('thiếu số lượng', { exact: true })).toBeVisible();
  await expect(dialog.getByRole('link', { name: 'Xem trang nguồn' })).toHaveAttribute('href', '/api/files/documents/17/pages/2');
  await dialog.getByRole('button', { name: 'Trang sau', exact: true }).click();
  await expect(dialog.getByText('Trang 2', { exact: true })).toBeVisible();
  await expect(dialog.getByRole('button', { name: 'Xác nhận phạm vi đầy đủ', exact: true })).toBeDisabled();
  await dialog.getByRole('button', { name: 'Kiểm tra dòng', exact: true }).click();
  await dialog.locator('input[name=value_text]').fill('19');
  await dialog.locator('textarea[name=fact_note]').fill('Đối chiếu trang nguồn');
  await dialog.getByRole('button', { name: 'Duyệt giá trị', exact: true }).click();
  await expect.poll(() => writes.length).toBe(1);
  expect(writes[0].body).toMatchObject({ action: 'approve', value_text: '19' });
  expect(writes[0].body).not.toHaveProperty('reviewer_id');
  await expect(dialog.getByText('thiếu số lượng', { exact: true })).toBeVisible();
  await dialog.getByRole('button', { name: 'Vật liệu, ký hiệu và bước lắp ráp', exact: true }).click();
  await expect(dialog.getByText('Vật liệu chưa thống nhất giữa tên chi tiết và bảng kê', { exact: true })).toBeVisible();
  await dialog.getByRole('button', { name: 'Kiểm tra trường', exact: true }).click();
  await dialog.locator('textarea[name=technical_value]').fill('Inox 201');
  await dialog.locator('textarea[name=technical_note]').fill('Đối chiếu bảng và chú thích');
  await dialog.getByRole('button', { name: 'Duyệt trường', exact: true }).click();
  await expect.poll(() => writes.length).toBe(2);
  expect(writes[1].body).toMatchObject({ action: 'approve', value_text: 'Inox 201' });
  expect(writes[1].body).not.toHaveProperty('reviewer_id');
  await expect(dialog.getByText('Inox 304', { exact: true }).first()).toBeVisible();
  await dialog.locator('textarea[name=sequence_numbers]').fill('1, 2, 3, 4, 5, 6');
  await dialog.locator('textarea[name=sequence_definition]').fill('Toàn bộ sáu bước trong bản vẽ');
  await dialog.locator('textarea[name=sequence_note]').fill('Đã đối chiếu toàn bộ bản vẽ');
  await dialog.getByRole('button', { name: 'Xác nhận trình tự đầy đủ', exact: true }).click();
  await expect.poll(() => writes.length).toBe(3);
  expect(writes[2].body).toMatchObject({ snapshot_id: 'snapshot-17', expected_step_numbers: [1,2,3,4,5,6] });
  expect(writes[2].body).not.toHaveProperty('approved');
  await expect(dialog.getByText('Trạng thái: Đã xác nhận đầy đủ.', { exact: false })).toBeVisible();
  await dialog.locator('form[data-sequence-review]').scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath('assembly-sequence-review.png'), fullPage: true });
});
