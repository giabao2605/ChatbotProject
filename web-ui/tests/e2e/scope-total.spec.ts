import { expect, test } from '@playwright/test';

for (const profile of ['evaluation','total','paired'] as const) {
test(`selected scope total renders its exact step and source (${profile})`, async ({ page, context }) => {
  const live = profile !== 'evaluation';
  const paired = profile === 'paired';
  let sent: Record<string, unknown> | undefined;
  const card = { doc_id: 17, version_no: 3, page_no: null, source_id: 'D17L1234567890abcdef',
    file_name: 'synthetic-ledger.csv', citation_schema: 'source-locator-v2', scope_id: 'ledger',
    scope_hash: 'b'.repeat(64), snapshot_id: 'synthetic-snapshot', source_hash: 'a'.repeat(64), fact_count: 2,
    locator: { doc_id: '17', version: '3', sheet: 'CSV', table_index: 1, row_key: '' },
    original_url: '/api/files/documents/17/original?scope_id=ledger' };
  await context.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    if (!url.pathname.startsWith('/api/')) return route.continue();
    if (url.pathname === '/api/chat/message') {
      sent = route.request().postDataJSON();
      if (sent?.scope_comparison) return route.fulfill({contentType:'text/event-stream',body:
        'event: delta\ndata: '+JSON.stringify({text:'Kết quả so sánh hai phạm vi đã chọn.'})+'\n\n'+
        'event: done\ndata: {}\n\n'});
      return route.fulfill({ contentType: 'text/event-stream', body:
        'event: delta\ndata: ' + JSON.stringify({ text: 'Tổng: 0.01 VND (trước làm tròn: 0.009 VND).' }) + '\n\n' +
        'event: done\ndata: ' + JSON.stringify({ advanced_rag: { math: {
          schema: 'grounded-calculation-scope-total-v1', template: 'sum_reviewed_scope',
          contract: live ? 'grounded-math-scope-total-controlled-demo-v1' : 'grounded-math-v2-scope-total-evaluation-v1',
          accepted: false, offline_only: !live, ...(live ? {activation_scope:'controlled_demo'} : {}),
          status: 'complete', domain_step_count: 1, scopes: [{ scope_id: 'ledger' }], sources: [card],
          steps: [{ step_id: 'sum', formula: 'sum(ledger)', value: '0.009', display_value: '0.01', unit: 'VND', status: 'valid' }],
        } } }) + '\n\n' });
    }
    let data: unknown = {};
    if (url.pathname === '/api/auth/me') data = { ok: true, user: { user_id: 7, username: 'synthetic-reviewer', roles: [live ? 'viewer' : 'reviewer'],
      allowed_departments: ['Accountant'], allowed_sites: ['factory'], max_security_level: 'internal', csrf_token: 'synthetic-csrf' } };
    else if (url.pathname.endsWith('/evaluation-capabilities')) data = paired ? {
      schema:'advanced-rag-v2-controlled-demo',contract:'math-reviewed-templates-controlled-demo-v1',
      accepted:false,offline_only:false,max_branches:1,actions:['scope_comparison','scope_total'],text_query:false
    } : live ? {
      schema:'advanced-rag-v2-controlled-demo',contract:'grounded-math-scope-total-controlled-demo-v1',
      accepted:false,offline_only:false,max_branches:1,actions:['scope_total'],text_query:false
    } : { schema: 'advanced-rag-v2-evaluation', accepted: false,
      offline_only: true, max_branches: 3, actions: ['contains', 'where_used', 'bom_rollup', 'scope_comparison', 'scope_total'] };
    else if (url.pathname === '/api/documents') data = { documents: [{ DocID: 17, OriginalFileName: 'synthetic-ledger.csv' }] };
    else if (url.pathname.endsWith('/approved-scopes')) {
      if (paired) expect(['scope_comparison','scope_total']).toContain(url.searchParams.get('template'));
      data = { items: [{ scope_id: 'ledger', definition: 'Whole reviewed export', unit: 'VND' },
        ...(paired && url.searchParams.get('template')==='scope_comparison' ? [{scope_id:'other',definition:'Other reviewed export',unit:'VND'}] : [])], offset: 0, has_more: false };
    }
    else if (url.pathname === '/api/chat/sessions') data = { sessions: [] };
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify(data) });
  });
  await page.goto('/chat');
  await expect(page.getByRole('heading', { name: 'Trợ lý tài liệu nội bộ', exact: true })).toBeVisible();
  await page.getByRole('checkbox', { name: paired ? 'Tính tổng hoặc so sánh có căn cứ (bật thử)' : live ? 'Tính tổng có căn cứ (bật thử)' : 'Chọn tác vụ có căn cứ (đánh giá)', exact: true }).check();
  await page.getByLabel('Tác vụ ý 1', { exact: true }).selectOption('scope_total');
  await page.getByLabel('Tài liệu Phạm vi tính tổng ý 1', { exact: true }).selectOption('17');
  await page.getByLabel('Phạm vi tính tổng ý 1', { exact: true }).selectOption('ledger');
  if (live) await expect(page.getByRole('button', { name: 'Thêm ý', exact: true })).toHaveCount(0);
  else await expect(page.getByRole('button', { name: 'Thêm ý', exact: true })).toBeDisabled();
  await page.locator('textarea').fill('Tổng phạm vi đã chọn');
  await page.getByRole('button', { name: 'Gửi', exact: true }).click();
  const result = page.getByRole('region', { name: 'Tổng một phạm vi', exact: true });
  await expect(result).toBeVisible();
  await expect(result).toContainText('0.009');
  await expect(result).toContainText('0.01');
  await expect(result).toContainText('sum(ledger)');
  expect(sent?.scope_total).toEqual({ scope_id: 'ledger' });
  expect(sent).not.toHaveProperty('query_plan');
  expect(sent).not.toHaveProperty('scope_comparison');
  await expect(result.getByRole('link')).toHaveCount(1);
  if (paired) {
    await page.getByLabel('Tác vụ ý 1',{exact:true}).selectOption('scope_comparison');
    for (const [label,scope] of [['A','ledger'],['B','other']]) {
      await page.getByLabel(`Tài liệu Phạm vi ${label} ý 1`,{exact:true}).selectOption('17');
      await page.getByLabel(`Phạm vi ${label} ý 1`,{exact:true}).selectOption(scope);
    }
    await page.locator('textarea').fill('So sánh hai phạm vi đã chọn');
    await page.getByRole('button',{name:'Gửi',exact:true}).click();
    await expect(page.getByText('Kết quả so sánh hai phạm vi đã chọn.',{exact:true})).toBeVisible();
    expect(sent?.scope_comparison).toEqual({scope_a:'ledger',scope_b:'other'});
    expect(sent).not.toHaveProperty('scope_total');expect(sent).not.toHaveProperty('query_plan');
    await page.screenshot({path:'../reports/rag-14-departments-20261005/math-templates-release-wiring-20261019/coexistence.png',fullPage:true});
  }
});
}

test('two explicit scope totals stay separate and the UI refuses a fourth branch', async ({ page, context }) => {
  let sent: Record<string, unknown> | undefined;
  const cards = ['cash', 'bank'].map((scope, index) => ({
    doc_id: 17, version_no: 3, page_no: null, source_id: `D17L${index ? '2' : '1'}234567890abcdef`,
    file_name: `${scope}.csv`, scope_id: scope, scope_hash: 'b'.repeat(64), snapshot_id: 'snapshot',
    original_url: `/api/files/documents/17/original?scope_id=${scope}`,
  }));
  await context.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    if (!url.pathname.startsWith('/api/')) return route.continue();
    if (url.pathname === '/api/chat/message') {
      sent = route.request().postDataJSON();
      return route.fulfill({ contentType: 'text/event-stream', body:
        'event: delta\ndata: ' + JSON.stringify({ text: 'Hai tổng có căn cứ.' }) + '\n\n' +
        'event: done\ndata: ' + JSON.stringify({ advanced_rag: { query: {
          schema: 'query-plan-scope-total-v1', contract: 'query-plan-scope-total-v1',
          status: 'complete', accepted: false, offline_only: true,
          branches: cards.map((card, index) => ({ branch_id: `b${index + 1}`, question: `Tổng ${card.scope_id}`,
            status: 'complete', sources: [card], math: { schema: 'grounded-calculation-scope-total-v1',
              steps: [{ step_id: 'sum', formula: `sum(${card.scope_id})`, value: index ? '2.345' : '1.235',
                display_value: index ? '2.35' : '1.24', unit: 'VND', status: 'valid' }] } })),
        } } }) + '\n\n' });
    }
    let data: unknown = {};
    if (url.pathname === '/api/auth/me') data = { ok: true, user: { user_id: 7, username: 'synthetic-reviewer', roles: ['reviewer'],
      allowed_departments: ['Accountant'], allowed_sites: ['factory'], max_security_level: 'internal', csrf_token: 'synthetic-csrf' } };
    else if (url.pathname.endsWith('/evaluation-capabilities')) data = { schema: 'advanced-rag-v2-evaluation', accepted: false,
      offline_only: true, max_branches: 3, scope_total_query: true,
      actions: ['contains', 'where_used', 'bom_rollup', 'scope_comparison', 'scope_total'] };
    else if (url.pathname === '/api/documents') data = { documents: [{ DocID: 17, OriginalFileName: 'synthetic-ledger.csv' }] };
    else if (url.pathname.endsWith('/approved-scopes')) data = { items: cards.map(card => ({
      scope_id: card.scope_id, definition: card.scope_id, unit: 'VND' })), offset: 0, has_more: false };
    else if (url.pathname === '/api/chat/sessions') data = { sessions: [] };
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify(data) });
  });
  await page.goto('/chat');
  await page.getByRole('checkbox', { name: 'Chọn tác vụ có căn cứ (đánh giá)', exact: true }).check();
  for (const [index, scope] of ['cash', 'bank'].entries()) {
    if (index) await page.getByRole('button', { name: 'Thêm ý', exact: true }).click();
    await page.getByLabel(`Tác vụ ý ${index + 1}`, { exact: true }).selectOption('scope_total');
    await page.getByLabel(`Tài liệu Phạm vi tính tổng ý ${index + 1}`, { exact: true }).selectOption('17');
    await page.getByLabel(`Phạm vi tính tổng ý ${index + 1}`, { exact: true }).selectOption(scope);
  }
  await page.getByRole('button', { name: 'Thêm ý', exact: true }).click();
  await expect(page.locator('fieldset')).toHaveCount(3);
  await expect(page.getByRole('button', { name: 'Thêm ý', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: 'Bỏ ý 3', exact: true }).click();
  await page.locator('textarea').fill('Tổng tiền mặt và tổng ngân hàng');
  await page.getByRole('button', { name: 'Gửi', exact: true }).click();
  const result = page.getByRole('region', { name: 'Kết quả từng ý', exact: true });
  await expect(result.locator('article')).toHaveCount(2);
  for (const [index, scope] of ['cash', 'bank'].entries()) {
    const branch = result.locator('article').nth(index);
    await expect(branch).toContainText(index ? '2.345' : '1.235');
    await expect(branch).toContainText(index ? '2.35' : '1.24');
    await branch.getByText(`Nguồn của ý ${index + 1}`, { exact: true }).click();
    await expect(branch.getByRole('link')).toHaveAttribute('href', `/api/files/documents/17/original?scope_id=${scope}`);
  }
  const branches = (sent?.query_plan as { branches: { scope_total: { scope_id: string } }[] }).branches;
  expect(branches.map(branch => branch.scope_total)).toEqual([{ scope_id: 'cash' }, { scope_id: 'bank' }]);
  expect(sent).not.toHaveProperty('scope_total');
  expect(sent).not.toHaveProperty('scope_comparison');
});
