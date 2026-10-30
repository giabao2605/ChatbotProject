import { expect, test } from '@playwright/test';

test('published drawing binding stays pending until a separate explicit review', async ({ page }, testInfo) => {
  const writes: { path: string; body: Record<string, unknown> }[] = [];
  let proposed = false;
  let approved = false;
  let reviewAttempts = 0;
  const binding = (id: number) => ({ BindingID: id, PartKey: id === 51 ? 'part:9.3.02414' : `part:fixture-${id}`,
    ProductScope: 'Chelsea', Site: 'factory', DocID: 18, VersionNo: 2,
    SourceDocID: 17, SourceVersion: 3, SourcePage: 2, SourceQuote: 'Chi tiết 9.3.02414 theo bản vẽ con',
    SourceHash: 'a'.repeat(64), SnapshotID: 'fixture-source-a', ReviewStatus: 'pending' });
  // Every API call is intercepted; no live document or review is read or written.
  await page.route(url => url.pathname.startsWith('/api/'), async route => {
    const request = route.request();
    const url = new URL(request.url());
    let body: unknown = {};
    if (request.method() !== 'GET') {
      if (!['/api/rag-evidence/graph/binding', '/api/rag-evidence/graph/binding/51/review'].includes(url.pathname)) return route.abort();
      writes.push({ path: url.pathname, body: request.postDataJSON() });
      if (url.pathname.endsWith('/review')) {
        reviewAttempts += 1;
        if (reviewAttempts === 1) body = { ok: false, reason: 'invalid_provenance' };
        else { approved = true; body = { ok: true, record_id: 51, status: 'approved' }; }
      } else { proposed = true; body = { ok: true, record_id: 51, status: 'pending' }; }
    } else if (url.pathname === '/api/auth/me') {
      body = { ok: true, user: { user_id: 7, username: 'offline-reviewer', roles: ['reviewer'], department: 'Technical',
        allowed_departments: ['Technical'], allowed_sites: ['factory'], max_security_level: 'internal',
        csrf_token: 'offline-csrf', preferred_language: 'vi' } };
    } else if (url.pathname === '/api/documents') {
      body = { documents: [
        { DocID: 17, OriginalFileName: 'Chelsea-assembly.pdf', VersionNo: 3, Site: 'factory', Department: 'Technical', IsCurrentVersion: 1 },
        { DocID: 18, OriginalFileName: 'Nut-M8.pdf', VersionNo: 2, Site: 'factory', Department: 'Technical', IsCurrentVersion: 1 },
      ] };
    } else if (url.pathname === '/api/rag-evidence/documents/17/scopes') {
      body = { scopes: [] };
    } else if (url.pathname === '/api/rag-evidence/documents/17/drawing-bindings') {
      const offset = Number(url.searchParams.get('offset') || 0);
      expect(url.searchParams.get('product_scope')).toBe('Chelsea');
      expect(url.searchParams.get('site')).toBe('factory');
      expect(url.searchParams.get('limit')).toBe('50');
      body = { items: offset === 0 ? Array.from({ length: 50 }, (_, i) => binding(i + 1))
        : proposed && !approved ? [binding(51)] : [], offset, limit: 50, has_more: offset === 0 && proposed && !approved };
    } else return route.fulfill({ status: 404, contentType: 'application/json', body: '{}' });
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  });

  await page.goto('/documents');
  await page.getByRole('button', { name: 'Kiểm tra chứng cứ', exact: true }).first().click();
  const dialog = page.getByRole('dialog', { name: 'Kiểm tra chứng cứ tài liệu', exact: true });
  await dialog.getByRole('button', { name: 'Liên kết chi tiết với bản vẽ', exact: true }).click();
  await dialog.getByLabel('Phạm vi sản phẩm', { exact: true }).fill('Chelsea');
  await dialog.getByRole('button', { name: 'Tải liên kết', exact: true }).click();
  await dialog.getByLabel('Mã chi tiết / cụm', { exact: true }).fill('9.3.02414');
  await dialog.getByRole('combobox', { name: 'Bản vẽ liên kết', exact: true }).selectOption('18');
  await dialog.getByLabel('Trang chứng cứ', { exact: true }).fill('2');
  await dialog.getByLabel('Trích dẫn chứng cứ', { exact: true }).fill('Chi tiết 9.3.02414 theo bản vẽ con');
  await dialog.getByLabel('Ghi chú đề xuất', { exact: true }).fill('Đã mở bản vẽ và đối chiếu mã');
  await dialog.getByRole('button', { name: 'Gửi đề xuất liên kết', exact: true }).click();
  await expect.poll(() => writes.length).toBe(1);
  expect(writes[0]).toEqual({ path: '/api/rag-evidence/graph/binding', body: {
    product_scope: 'Chelsea', site: 'factory', note: 'Đã mở bản vẽ và đối chiếu mã', payload: {
      part_key: 'part:9.3.02414', doc_id: 18, version_no: 2, source_doc_id: 17, source_version: 3,
      source_page: 2, source_quote: 'Chi tiết 9.3.02414 theo bản vẽ con',
    },
  } });
  expect(approved).toBe(false);
  await dialog.getByRole('button', { name: 'Trang liên kết sau', exact: true }).click();
  await expect(dialog.getByRole('cell', { name: /part:9\.3\.02414/ })).toBeVisible();
  await dialog.getByRole('button', { name: 'Kiểm tra liên kết', exact: true }).click();
  await expect(dialog.locator('a[href="/api/files/documents/17/pages/2"]').first()).toBeVisible();
  await expect(dialog.locator('a[href="/api/files/documents/18/original"]').first()).toBeVisible();
  const approve = dialog.getByRole('button', { name: 'Duyệt liên kết', exact: true });
  await dialog.getByLabel('Ghi chú duyệt liên kết', { exact: true }).fill('Đối chiếu đủ hai tài liệu và mã chi tiết');
  await expect(approve).toBeDisabled();
  await dialog.getByLabel('Tôi đã đối chiếu mã, bản vẽ, phiên bản và phạm vi sản phẩm', { exact: true }).check();
  await approve.scrollIntoViewIfNeeded();
  await dialog.screenshot({ path: testInfo.outputPath('pending-drawing-binding-review.png') });
  await approve.click();
  await expect(dialog.getByRole('alert')).toContainText(/nguồn|căn cứ/i);
  expect(approved).toBe(false);
  expect(writes).toHaveLength(2);
  await approve.click();
  await expect.poll(() => writes.length).toBe(3);
  expect(writes[2]).toEqual({ path: '/api/rag-evidence/graph/binding/51/review', body: {
    action: 'approve', product_scope: 'Chelsea', site: 'factory', note: 'Đối chiếu đủ hai tài liệu và mã chi tiết',
  } });
  await expect(approve).not.toBeVisible();
  expect(approved).toBe(true);
  await dialog.screenshot({ path: testInfo.outputPath('drawing-binding-review.png') });
});
