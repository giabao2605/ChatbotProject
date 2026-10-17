import { expect, test } from '@playwright/test';

for (const [choice, linkedStep] of [['step:step-6', 'step-6'], ['document', null], ['bom:symbol-51', null]] as const) {
  test(`offline drawing-note review requires an explicit ${choice} choice`, async ({ page }, testInfo) => {
    const writes: Record<string, unknown>[] = [];
    let approved = false;
    // Every API request is intercepted; this test never reads or writes a live service.
    await page.route(url => url.pathname.startsWith('/api/'), async route => {
      const req = route.request();
      const path = new URL(req.url()).pathname;
      let body: unknown = {};
      if (req.method() !== 'GET') {
        if (path !== '/api/rag-evidence/text-facts/note-1/review') return route.abort();
        const decision = req.postDataJSON();
        writes.push(decision);
        if (writes.length === 1) body = { ok: false, error: 'Nguồn đã đổi; hãy đối chiếu lại' };
        else { approved = true; body = { ok: true }; }
      } else if (path === '/api/auth/me') {
        body = { ok: true, user: { user_id: 7, username: 'offline-reviewer', roles: ['reviewer'], department: 'Technical',
          allowed_departments: ['Technical'], allowed_sites: ['HQ'], max_security_level: 'internal',
          csrf_token: 'offline-csrf', preferred_language: 'vi' } };
      } else if (path === '/api/documents/pending-review') {
        body = { documents: [{ DocID: 17, JobID: 1, TenFile: 'drawing-note-fixture.pdf', ThuMuc: 'Technical' }] };
      } else if (path.endsWith('/scopes')) {
        body = { scopes: [] };
      } else if (path.endsWith('/text-facts')) {
        body = { items: [{ fact_id: 'note-1', kind: 'drawing_note', label: 'Ghi chú hàn', raw_value: 'Hàn đầy', value: 'Hàn đầy',
          review_status: approved ? 'approved' : 'pending', linked_step_fact_id: approved ? linkedStep : null,
          linked_bom_fact_id: approved && choice.startsWith('bom:') ? 'symbol-51' : null, linked_bom_status: approved && choice.startsWith('bom:') ? 'valid' : null,
          candidates: [], conflicts: approved ? [] : ['drawing_note_boundary_requires_review', 'drawing_note_association_requires_review'],
          locator: { doc_id: '17', version: '3', page: 1, bbox: [10, 20, 30, 40], row_key: 'note-region-1' } }],
          offset: 0, has_more: false };
      } else if (path.endsWith('/bom-reference-options')) {
        const offset = Number(new URL(req.url()).searchParams.get('offset'));
        body = { items: [{ fact_id: offset ? 'symbol-51' : 'symbol-1', label: 'Nut M8', bom_position: offset ? '51' : '1',
          locator: { doc_id: '17', version: '3', page: 2 } }], offset, limit: 50, has_more: offset === 0 };
      } else if (path.endsWith('/assembly-sequence')) {
        body = { status: 'unknown', step_options: [
          { fact_id: 'step-6', sequence_number: 6, label: 'Xiết chân', review_status: 'approved' },
          { fact_id: 'step-pending', sequence_number: 7, label: 'Chưa đối chiếu', review_status: 'pending' },
        ] };
      }
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
    });
    await page.goto('/review');
    await page.getByRole('button', { name: 'Duyệt dữ liệu có cấu trúc', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: 'Duyệt dữ liệu có cấu trúc' });
    await dialog.getByRole('button', { name: 'Vật liệu, ký hiệu và bước lắp ráp', exact: true }).click();
    await expect(dialog.getByText('Chưa gắn bước', { exact: true })).toBeVisible();
    await expect(dialog.getByRole('link', { name: 'Xem trang nguồn', exact: true })).toHaveAttribute('href', '/api/files/documents/17/pages/1');
    await expect(dialog.getByText('Cần đối chiếu đầy đủ vùng chú thích trước khi duyệt', { exact: true })).toBeVisible();
    await dialog.getByRole('button', { name: 'Kiểm tra trường', exact: true }).click();
    const select = dialog.getByRole('combobox', { name: 'Phạm vi liên quan của chú thích', exact: true });
    await expect(select).toHaveValue('');
    await expect(select.locator('option[value="step:step-pending"]')).toHaveJSProperty('disabled', true);
    await dialog.getByLabel('Ghi chú kiểm tra', { exact: true }).fill('Đã đối chiếu đầy đủ vùng nguồn');
    await dialog.getByRole('button', { name: 'Duyệt trường', exact: true }).click();
    expect(writes).toHaveLength(0);
    if (choice.startsWith('bom:')) {
      await dialog.getByRole('button', { name: 'Tải dòng bảng kê', exact: true }).click();
      await dialog.getByRole('button', { name: 'Tải thêm dòng bảng kê', exact: true }).click();
    }
    await select.selectOption(choice);
    if (choice.startsWith('bom:')) {
      await expect(dialog.getByRole('link', { name: 'Xem trang nguồn bảng kê', exact: true })).toHaveAttribute('href', '/api/files/documents/17/pages/2');
      await select.scrollIntoViewIfNeeded();
      await dialog.screenshot({ path: testInfo.outputPath('bom-selection.png') });
    }
    await dialog.getByRole('button', { name: 'Duyệt trường', exact: true }).click();
    await expect(dialog.getByRole('alert')).toContainText('Nguồn đã đổi');
    await expect(dialog.getByText('Chưa gắn bước', { exact: true })).toBeVisible();
    expect(writes).toHaveLength(1);
    await dialog.getByRole('button', { name: 'Duyệt trường', exact: true }).click();
    await expect(dialog.getByText('Đã lưu quyết định duyệt trường kỹ thuật.', { exact: true })).toBeVisible();
    expect(writes).toHaveLength(2);
    expect(writes[1]).toEqual({ action: 'approve', value_text: 'Hàn đầy', note: 'Đã đối chiếu đầy đủ vùng nguồn', linked_step_fact_id: linkedStep, linked_bom_fact_id: choice.startsWith('bom:') ? 'symbol-51' : null });
    await dialog.getByRole('button', { name: 'Kiểm tra trường', exact: true }).click();
    await expect(select).toHaveValue(choice.startsWith('bom:') ? '' : choice);
    await expect(dialog.getByLabel('Ghi chú kiểm tra', { exact: true })).toHaveValue('');
    await page.screenshot({ path: testInfo.outputPath('drawing-note-review.png'), fullPage: true });
    expect(writes).toHaveLength(2);
  });
}
